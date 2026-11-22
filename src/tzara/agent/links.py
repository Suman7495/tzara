"""Keeping made-up links out of replies.

A small model writes a plausible address as easily as a real one: a real shop's domain with
a product path that does not exist. So a link may appear in a reply only if the agent actually
saw that address, in a tool result or in the user's own message, during the turn. Anything else
is taken out before the user sees it, and what remains reads as ordinary text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import ClassVar
from urllib.parse import urlparse


class TrustedLinks:
    """The web addresses the agent has really seen during a turn.

    Two addresses count as the same page when they have the same site and path: a different
    query string or fragment is the same page, and a different path is not.
    """

    ADDRESS: ClassVar[re.Pattern[str]] = re.compile(
        r"https?://[^\s<>\"'`\]\[)(]+(?:\([^\s<>\"'`)(]*\))?[^\s<>\"'`\]\[)(]*", re.IGNORECASE
    )
    TRAILING: ClassVar[str] = ".,;:!?*_"

    def __init__(self) -> None:
        """Start with no addresses."""
        self._seen: set[str] = set()

    def clear(self) -> None:
        """Forget every address: a new turn starts with none."""
        self._seen.clear()

    def collect(self, text: str) -> None:
        """Remember every address written in ``text``."""
        for match in self.ADDRESS.findall(text):
            key = self.key(match.rstrip(self.TRAILING))
            if key:
                self._seen.add(key)

    def trusts(self, url: str) -> bool:
        """Whether ``url`` is a page the agent has seen."""
        return self.key(url.strip().rstrip(self.TRAILING)) in self._seen

    @staticmethod
    def key(url: str) -> str:
        """What identifies a page: its site without ``www.``, and its path without a final slash."""
        parts = urlparse(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return ""
        return parts.hostname.lower().removeprefix("www.") + parts.path.rstrip("/")


class LinkGuard:
    """Removes links the agent did not see from a reply that is being streamed.

    A reply arrives a few characters at a time, and a link can be split anywhere, so the end of
    what has arrived is held back while it could still turn into a link, and let go once it is
    clear that it is, or is not, one. A markdown link that is not trusted keeps its text; a bare
    address that is not trusted disappears.
    """

    MAX_TEXT: ClassVar[int] = 300
    MAX_ADDRESS: ClassVar[int] = 2000
    SCHEMES: ClassVar[tuple[str, ...]] = ("https://", "http://")

    def __init__(self, trusts: Callable[[str], bool]) -> None:
        """Create a guard.

        Args:
            trusts: Says whether an address is one the agent saw.
        """
        self._trusts = trusts
        self._buffer = ""

    def feed(self, piece: str) -> str:
        """Take the next piece of the reply and return what can be shown now."""
        self._buffer += piece
        return self._drain(final=False)

    def finish(self) -> str:
        """The reply is over: return everything still held back, checked as it stands."""
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        """Move as much of the buffer as is settled into the output."""
        out: list[str] = []
        position = 0
        text = self._buffer
        while position < len(text):
            start = self._next_start(text, position)
            if start is None:
                kept = text[position:] if final else text[position : self._safe_end(text, position)]
                out.append(kept)
                position += len(kept)
                break
            out.append(text[position:start])
            position = start
            consumed, shown = self._take(text, start, final)
            if consumed == 0:  # not decidable yet: wait for more
                break
            out.append(shown)
            position = start + consumed
        self._buffer = text[position:]
        return "".join(out)

    def _next_start(self, text: str, position: int) -> int | None:
        """Where the next possible link begins at or after ``position``, if anywhere."""
        candidates = [
            i for i in (text.find("[", position), self._find_scheme(text, position)) if i >= 0
        ]
        return min(candidates) if candidates else None

    def _find_scheme(self, text: str, position: int) -> int:
        """Where ``http://`` or ``https://`` begins at or after ``position``, or -1."""
        lowered = text.lower()
        found = [i for i in (lowered.find(s, position) for s in self.SCHEMES) if i >= 0]
        return min(found) if found else -1

    def _safe_end(self, text: str, position: int) -> int:
        """How far text can be shown when no link has started: not into half a scheme."""
        lowered = text.lower()
        for length in range(min(len("https://") - 1, len(text) - position), 0, -1):
            tail = lowered[len(text) - length :]
            if any(scheme.startswith(tail) for scheme in self.SCHEMES):
                return len(text) - length
        return len(text)

    def _take(self, text: str, start: int, final: bool) -> tuple[int, str]:
        """Decide one possible link at ``start``.

        Returns:
            How many characters were settled and what to show for them, or ``(0, "")`` if more text
            is needed to tell.
        """
        if text[start] == "[":
            return self._take_markdown(text, start, final)
        return self._take_address(text, start, final)

    def _take_markdown(self, text: str, start: int, final: bool) -> tuple[int, str]:
        """Settle ``[text](address)``, or a ``[`` that is not the start of one."""
        close = text.find("]", start)
        if close < 0:
            waiting = not final and "\n" not in text[start:] and len(text) - start <= self.MAX_TEXT
            return (0, "") if waiting else (1, "[")
        if "\n" in text[start:close] or close - start > self.MAX_TEXT:
            return 1, "["
        return self._take_target(text, start, close, final)

    def _take_target(self, text: str, start: int, close: int, final: bool) -> tuple[int, str]:
        """Settle what follows the ``]`` that closes the text at ``start``."""
        plain = (close + 1 - start, text[start : close + 1])
        if close + 1 >= len(text):
            return plain if final else (0, "")
        if text[close + 1] != "(":
            return plain
        end = self._matching_parenthesis(text, close + 1)
        if end < 0:
            waiting = (
                not final and len(text) - close <= self.MAX_ADDRESS and "\n" not in text[close:]
            )
            return (0, "") if waiting else plain
        address = text[close + 2 : end]
        label = text[start + 1 : close]
        return end + 1 - start, (text[start : end + 1] if self._trusts(address) else label)

    @staticmethod
    def _matching_parenthesis(text: str, opening: int) -> int:
        """The index of the ``)`` that closes the ``(`` at ``opening``, or -1."""
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    return index
            elif text[index].isspace():
                return -1
        return -1

    def _take_address(self, text: str, start: int, final: bool) -> tuple[int, str]:
        """Settle a bare address: keep it if trusted, otherwise drop it."""
        end = start
        while end < len(text) and not text[end].isspace() and text[end] not in "<>\"'`]":
            end += 1
        if end >= len(text) and not final and len(text) - start <= self.MAX_ADDRESS:
            return 0, ""
        raw = text[start:end]
        address = raw
        while address and address[-1] in TrustedLinks.TRAILING + ")":
            if address[-1] == ")" and address.count("(") >= address.count(")"):
                break
            address = address[:-1]
        trailing = raw[len(address) :]
        return end - start, (address if self._trusts(address) else "") + trailing
