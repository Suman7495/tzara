"""Edits to a text that need no model: delete the second paragraph, replace X with Y."""

from __future__ import annotations

import re
from typing import ClassVar


class TextEdit:
    """Applies the mechanical edits a person asks for in plain words, and says "no" for the rest.

    qwen3:8b, asked to "delete the second paragraph" of a text, gives the text back unchanged, or
    rewrites it. These edits are exact, so they are done here, in code: the model is only asked for
    what needs language (make it friendlier, shorter). :meth:`apply` returns ``None`` when the
    instruction is not one it knows, or its target is not in the text, and the model takes over.
    """

    ORDINALS: ClassVar[dict[str, int]] = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
        "last": -1,
        "final": -1,
    }
    DELETE: ClassVar[str] = r"(?:delete|remove|drop|erase|cut|get rid of|take out)"
    POLITE: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:(?:please|can you|could you|would you|just|hey)\s+)+", re.IGNORECASE
    )
    UNITS: ClassVar[str] = r"(?:paragraph|para|sentence|line)"
    SIGN_OFF: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:best|regards|kind regards|best regards|warm regards|thanks|thank you|cheers|"
        r"sincerely|yours|many thanks|with thanks|talk soon|take care)\b",
        re.IGNORECASE,
    )
    GREETING: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:hi|hello|hey|dear|good (?:morning|afternoon|evening))\b", re.IGNORECASE
    )

    @classmethod
    def clean(cls, instruction: str) -> str:
        """The instruction without polite words in front, one line."""
        return cls.POLITE.sub("", " ".join(instruction.strip().split())).rstrip(".!")

    @classmethod
    def deletes_the_selection(cls, instruction: str) -> bool:
        """Whether the instruction just says to delete what is selected ("delete this")."""
        text = cls.clean(instruction).lower()
        return bool(
            re.fullmatch(
                rf"{cls.DELETE}(?: (?:this|that|it|the selection|"
                rf"the selected (?:text|part|paragraph)|the {cls.UNITS}))?",
                text,
            )
        )

    @classmethod
    def apply(cls, instruction: str, text: str) -> str | None:
        """``text`` with the edit asked for, or ``None`` if ``instruction`` is not one of these."""
        original = cls.clean(instruction)
        lowered = original.lower()
        result = (
            cls._clear(lowered)
            or cls._numbered(lowered, text)
            or cls._about(lowered, text)
            or cls._greeting_or_sign_off(lowered, text)
            or cls._replace(original, text)
        )
        if result is None:
            return None
        edited = result.value
        return None if edited == text else edited

    # -- each kind of edit; a result is wrapped so that an empty text is not "no result" ----------

    class _Result:
        def __init__(self, value: str) -> None:
            self.value = value

        def __bool__(self) -> bool:
            return True

    @classmethod
    def _clear(cls, lowered: str) -> _Result | None:
        if re.fullmatch(
            rf"{cls.DELETE} (?:everything|all|all of it|the whole thing|it all|all the text)"
            r"|clear (?:it|all|everything)",
            lowered,
        ):
            return cls._Result("")
        return None

    @classmethod
    def _numbered(cls, lowered: str, text: str) -> _Result | None:
        ordinal = "|".join([*cls.ORDINALS, r"\d+(?:st|nd|rd|th)?"])
        found = re.fullmatch(
            rf"{cls.DELETE} (?:the )?(?P<which>{ordinal}) (?P<unit>{cls.UNITS})", lowered
        ) or re.fullmatch(rf"{cls.DELETE} (?P<unit>{cls.UNITS}) (?P<which>\d+)", lowered)
        if not found:
            return None
        which = found.group("which")
        number = cls.ORDINALS.get(which) or int(re.sub(r"\D", "", which))
        return cls._remove(found.group("unit"), text, lambda units: cls._pick(units, number))

    @classmethod
    def _about(cls, lowered: str, text: str) -> _Result | None:
        found = re.fullmatch(
            rf"{cls.DELETE} (?:the )?(?P<unit>{cls.UNITS}) (?:about|on|containing|with|mentioning|"
            r"that (?:mentions|says|talks about|is about)) (?P<what>.+)",
            lowered,
        )
        if not found:
            return None
        words = [
            w for w in re.findall(r"[\w'-]{3,}", found.group("what")) if w not in {"the", "and"}
        ]
        if not words:
            return None

        def choose(units: list[str]) -> int | None:
            scores = [sum(w in unit.lower() for w in words) for unit in units]
            best = max(scores, default=0)
            if best == 0 or scores.count(best) != 1:
                return None  # nothing mentions it, or the choice is not clear: not guessed
            return scores.index(best)

        return cls._remove(found.group("unit"), text, choose)

    @classmethod
    def _greeting_or_sign_off(cls, lowered: str, text: str) -> _Result | None:
        paragraphs = cls._paragraphs(text)
        if re.fullmatch(rf"{cls.DELETE} (?:the )?(?:greeting|salutation)", lowered):
            if paragraphs and cls.GREETING.match(paragraphs[0]) and len(paragraphs[0]) < 60:
                return cls._Result("\n\n".join(paragraphs[1:]))
        elif re.fullmatch(
            rf"{cls.DELETE} (?:the )?(?:sign-?off|sign off|signature|closing)", lowered
        ):
            for start in range(len(paragraphs) - 1, 0, -1):
                if cls.SIGN_OFF.match(paragraphs[start]) and len(paragraphs[start]) < 120:
                    return cls._Result("\n\n".join(paragraphs[:start]))
        return None

    @classmethod
    def _replace(cls, original: str, text: str) -> _Result | None:
        found = re.fullmatch(
            r"(?:replace|change|swap)\s+[\"\u201c'\u2018]?(?P<old>.+?)[\"\u201d'\u2019]?\s+(?:with|to|by|for)\s+"
            r"[\"\u201c'\u2018]?(?P<new>.+?)[\"\u201d'\u2019]?",
            original,
            re.IGNORECASE,
        )
        if not found:
            return None
        pattern = re.compile(re.escape(found.group("old")), re.IGNORECASE)
        if not pattern.search(text):
            return None  # "change the tone to friendly" names no text: that is for the model
        return cls._Result(pattern.sub(lambda _: found.group("new"), text))

    # -- units ----------------------------------------------------------------------------------

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", text.strip())]
        blocks = [block for block in blocks if block]
        if len(blocks) == 1 and "\n" in blocks[0]:  # lines instead of blank-line paragraphs
            blocks = [line.strip() for line in blocks[0].split("\n") if line.strip()]
        return blocks

    @staticmethod
    def _pick(units: list[str], number: int) -> int | None:
        index = len(units) - 1 if number == -1 else number - 1
        return index if 0 <= index < len(units) else None

    @classmethod
    def _remove(cls, unit: str, text: str, choose) -> _Result | None:  # type: ignore[no-untyped-def]
        if unit in {"paragraph", "para"}:
            paragraphs = cls._paragraphs(text)
            index = choose(paragraphs)
            if index is None:
                return None
            joiner = "\n\n" if re.search(r"\n\s*\n", text) else "\n"
            return cls._Result(joiner.join(paragraphs[:index] + paragraphs[index + 1 :]))
        if unit == "line":
            lines = [line for line in text.split("\n") if line.strip()]
            index = choose(lines)
            if index is None:
                return None
            kept = lines[:index] + lines[index + 1 :]
            return cls._Result("\n".join(kept))
        spans = [m.span() for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)\s*", text)]
        index = choose([text[start:end] for start, end in spans])
        if index is None:
            return None
        start, end = spans[index]
        return cls._Result((text[:start] + text[end:]).strip())
