"""Edits to a text that need no model: delete the second paragraph, replace X with Y."""

from __future__ import annotations

import re

from tzara.config import WriteSettings


class TextEdit:
    """Applies the mechanical edits a person asks for in plain words, and says "no" for the rest.

    qwen3:8b, asked to "delete the second paragraph" of a text, gives the text back unchanged, or
    rewrites it. These edits are exact, so they are done here, in code: the model is only asked for
    what needs language (make it friendlier, shorter). :meth:`apply` returns ``None`` when the
    instruction is not one it knows, or its target is not in the text, and the model takes over.
    """

    def __init__(self, settings: WriteSettings) -> None:
        """Create the editor.

        Args:
            settings: The words that name a verb, a part of a text and a place in it.
        """
        flags = re.IGNORECASE
        self._ordinals = settings.edit_ordinals
        self._delete = settings.edit_delete
        self._units = settings.edit_units
        self._polite = re.compile(settings.edit_polite, flags)
        self._sign_off = re.compile(settings.edit_sign_off, flags)
        self._greeting = re.compile(settings.edit_greeting, flags)

    def clean(self, instruction: str) -> str:
        """The instruction without polite words in front, one line."""
        return self._polite.sub("", " ".join(instruction.strip().split())).rstrip(".!")

    def deletes_the_selection(self, instruction: str) -> bool:
        """Whether the instruction just says to delete what is selected ("delete this")."""
        text = self.clean(instruction).lower()
        return bool(
            re.fullmatch(
                rf"{self._delete}(?: (?:this|that|it|the selection|"
                rf"the selected (?:text|part|paragraph)|the {self._units}))?",
                text,
            )
        )

    def target_missing(self, instruction: str, text: str) -> bool:
        """Whether the instruction names a paragraph, sentence or line the text does not have.

        "Delete the ninth paragraph" of a text of three: there is nothing to do, and the model must
        not be asked to improvise (it rewrote the text). The answer is "nothing changed".
        """
        lowered = self.clean(instruction).lower()
        ordinal = "|".join([*self._ordinals, r"\d+(?:st|nd|rd|th)?"])
        found = re.fullmatch(
            rf"{self._delete} (?:the )?(?P<which>{ordinal}) (?P<unit>{self._units})", lowered
        ) or re.fullmatch(rf"{self._delete} (?P<unit>{self._units}) (?P<which>\d+)", lowered)
        if not found:
            return False
        which = found.group("which")
        number = self._ordinals.get(which) or int(re.sub(r"\D", "", which))
        unit = found.group("unit")
        if unit in {"paragraph", "para"}:
            count = len(self._paragraphs(text))
        elif unit == "line":
            count = len([line for line in text.split("\n") if line.strip()])
        else:
            count = len(re.findall(r"[^.!?\n]+(?:[.!?]+|$)\s*", text))
        return number != -1 and number > count

    def apply(self, instruction: str, text: str) -> str | None:
        """``text`` with the edit asked for, or ``None`` if ``instruction`` is not one of these."""
        original = self.clean(instruction)
        lowered = original.lower()
        result = (
            self._clear(lowered)
            or self._numbered(lowered, text)
            or self._about(lowered, text)
            or self._greeting_or_sign_off(lowered, text)
            or self._replace(original, text)
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

    def _clear(self, lowered: str) -> _Result | None:
        if re.fullmatch(
            rf"{self._delete} (?:everything|all|all of it|the whole thing|it all|all the text)"
            r"|clear (?:it|all|everything)",
            lowered,
        ):
            return self._Result("")
        return None

    def _numbered(self, lowered: str, text: str) -> _Result | None:
        ordinal = "|".join([*self._ordinals, r"\d+(?:st|nd|rd|th)?"])
        found = re.fullmatch(
            rf"{self._delete} (?:the )?(?P<which>{ordinal}) (?P<unit>{self._units})", lowered
        ) or re.fullmatch(rf"{self._delete} (?P<unit>{self._units}) (?P<which>\d+)", lowered)
        if not found:
            return None
        which = found.group("which")
        number = self._ordinals.get(which) or int(re.sub(r"\D", "", which))
        return self._remove(found.group("unit"), text, lambda units: self._pick(units, number))

    def _about(self, lowered: str, text: str) -> _Result | None:
        found = re.fullmatch(
            rf"{self._delete} (?:the )?(?P<unit>{self._units}) "
            r"(?:about|on|containing|with|mentioning|"
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

        return self._remove(found.group("unit"), text, choose)

    def _greeting_or_sign_off(self, lowered: str, text: str) -> _Result | None:
        paragraphs = self._paragraphs(text)
        if re.fullmatch(rf"{self._delete} (?:the )?(?:greeting|salutation)", lowered):
            if paragraphs and self._greeting.match(paragraphs[0]) and len(paragraphs[0]) < 60:
                return self._Result("\n\n".join(paragraphs[1:]))
        elif re.fullmatch(
            rf"{self._delete} (?:the )?(?:sign-?off|sign off|signature|closing)", lowered
        ):
            for start in range(len(paragraphs) - 1, 0, -1):
                if self._sign_off.match(paragraphs[start]) and len(paragraphs[start]) < 120:
                    return self._Result("\n\n".join(paragraphs[:start]))
        return None

    def _replace(self, original: str, text: str) -> _Result | None:
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
        return self._Result(pattern.sub(lambda _: found.group("new"), text))

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

    def _remove(self, unit: str, text: str, choose) -> _Result | None:  # type: ignore[no-untyped-def]
        if unit in {"paragraph", "para"}:
            paragraphs = self._paragraphs(text)
            index = choose(paragraphs)
            if index is None:
                return None
            joiner = "\n\n" if re.search(r"\n\s*\n", text) else "\n"
            return self._Result(joiner.join(paragraphs[:index] + paragraphs[index + 1 :]))
        if unit == "line":
            lines = [line for line in text.split("\n") if line.strip()]
            index = choose(lines)
            if index is None:
                return None
            kept = lines[:index] + lines[index + 1 :]
            return self._Result("\n".join(kept))
        spans = [m.span() for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)\s*", text)]
        index = choose([text[start:end] for start, end in spans])
        if index is None:
            return None
        start, end = spans[index]
        return self._Result((text[:start] + text[end:]).strip())
