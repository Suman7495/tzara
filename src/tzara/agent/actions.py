"""What the agent did in a reply, kept in the conversation so the next message knows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class ActionRecord:
    """One tool the agent ran.

    Attributes:
        tool: The tool's name, such as ``notion__notion-update-page``.
        target: What it was about (a page address, a search, a file name), short, or empty.
        result: ``ok``, or ``failed: `` and the start of the error.
    """

    tool: str
    target: str
    result: str

    @property
    def failed(self) -> bool:
        """Whether the tool reported an error."""
        return self.result.startswith("failed")


class ActionNote:
    """Writes the record of a reply's actions after the reply, and takes it out for display.

    The model's own memory of a past reply is only its words. After "yes", "expand it" or "did
    that work?" it must know which page it changed, with what result, and that is not in the
    words. The note goes at the end of the stored reply: the user never sees it
    (:meth:`strip`), and it is also what a restart brings back.
    """

    START: ClassVar[str] = "[Actions in that reply:"
    END: ClassVar[str] = "]"
    BLOCK: ClassVar[re.Pattern[str]] = re.compile(
        r"\n*\[Actions in that reply:.*?\]\s*\Z", re.DOTALL
    )
    QUIET: ClassVar[frozenset[str]] = frozenset({"recall"})

    @classmethod
    def render(cls, records: list[ActionRecord]) -> str:
        """The note for ``records``, or empty if nothing worth noting was done."""
        lines = [
            f"{record.tool}{f' on {record.target}' if record.target else ''}: {record.result}"
            for record in records
            if record.tool not in cls.QUIET
        ]
        return f"\n\n{cls.START} " + "; ".join(lines) + cls.END if lines else ""

    @classmethod
    def strip(cls, text: str) -> str:
        """``text`` without the note at its end."""
        return cls.BLOCK.sub("", text)
