"""A tool for the model to read what is on the user's screen when the request needs it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from tzara.agent.screen import ScreenContext
from tzara.tools.web import Sanitizer


class ReadScreenInput(BaseModel):
    """Arguments accepted by the read_screen tool."""

    what: Literal["all", "window", "typing", "selection", "page"] = Field(
        default="all",
        description="window: the window's text; typing: the field being written in; "
        "selection: selected text; page: the open web page or document.",
    )


class ReadScreen(BaseTool):
    """Reads the screen context of the current message, on request.

    Which parts of the screen go into a message with it is decided by rules (a selection, "this"
    in the words). Rules cannot read every way of asking ("what amount is due", "handle it"), so
    the rest is here for the model to ask for: the message says what is available, and the model
    calls this when its request is about it. Nothing is read from the screen at that moment: it
    returns what the Mac app sent with the message.
    """

    name: str = "read_screen"
    description: str = "Read what is on the user's screen."
    args_schema: type[BaseModel] = ReadScreenInput

    def __init__(
        self,
        description: str,
        context: Callable[[], ScreenContext | None],
        page: Callable[[ScreenContext], str],
        limits: tuple[int, int],
        notice: str,
    ) -> None:
        """Create the tool.

        Args:
            description: What the model is told the tool does.
            context: The screen context that came with the current message.
            page: Reads the page or document behind the context (as the page rules do).
            limits: Most characters of window text and of other text returned.
            notice: Put in front of the text: it is data from the screen, not instructions.
        """
        super().__init__(description=description)
        self._context = context
        self._page = page
        self._limits = limits
        self._notice = notice

    def _run(self, what: str = "all") -> str:
        """Return the asked-for part of the screen, or say there is nothing."""
        context = self._context()
        if context is None or context.empty:
            return "Nothing was read from the screen for this message."
        window_limit, other_limit = self._limits
        parts: list[str] = []
        if what in {"all", "selection"} and context.selection:
            parts.append("Selected text:\n" + self._cut(context.selection, other_limit))
        if what in {"all", "typing"} and context.typing:
            parts.append("Typing here:\n" + self._cut(context.typing, other_limit))
        if what in {"all", "window"} and context.visible:
            parts.append("Window text:\n" + self._cut(context.visible, window_limit))
        if what == "page":
            parts.append("Page text:\n" + self._cut(self._page(context), window_limit))
        if not parts:
            return "There is nothing of that kind on the screen."
        return self._notice + "\n" + "\n\n".join(parts)

    @staticmethod
    def _cut(text: str, limit: int) -> str:
        cleaned = Sanitizer.clean(text).strip()
        return cleaned if len(cleaned) <= limit else cleaned[:limit].rstrip() + " [cut]"
