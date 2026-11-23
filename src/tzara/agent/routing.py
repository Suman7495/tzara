"""Deciding in code that a question needs the web, instead of hoping the model asks for it."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from tzara.agent.screen import ScreenBlock
from tzara.config import ResearchSettings


class QuestionRules:
    """Says from the words alone whether a message asks for facts from the web.

    A small model told to research factual and buying questions does so only some of the time:
    asked for a good keyboard it answered from memory and named sources it had not read. Asking
    the model whether to research costs a second request, and that makes Ollama read the whole
    conversation again, which is several seconds. Rules cost nothing and give the same answer
    every time. They err towards research for questions; chat, arithmetic, questions about the
    user and commands for the Mac are not researched.
    """

    def __init__(self, settings: ResearchSettings) -> None:
        """Create the rules.

        Args:
            settings: The words and patterns that decide.
        """
        self._starts = settings.question_starts
        self._words = settings.question_words
        self._chat = [re.compile(pattern, re.IGNORECASE) for pattern in settings.chat_patterns]

    def needs_sources(self, message: str) -> bool:
        """Whether ``message`` should be answered from web pages."""
        text = message.strip().lower()
        if not text or any(pattern.search(text) for pattern in self._chat):
            return False
        words = text.split()
        return (
            "?" in text
            or words[0] in self._starts
            or any(word in f" {text} " for word in self._words)
        )


class ResearchRouter(AgentMiddleware):
    """Makes the research call itself when a message asks for facts from the web.

    Only the first model call of a turn is routed, when the last message is the user's. The call
    that follows the routed result is the model's to make, with the same tools as every other call:
    taking them away changes the start of the prompt, and Ollama then reads all of it again, which
    costs seconds, here and on the next message. If the model asks for another search anyway, that
    one call is made again without tools, so it writes the answer from the evidence it has.
    """

    ROUTED: ClassVar[str] = "route-"

    def __init__(
        self,
        rules: QuestionRules,
        tool: str = "research",
        report: Callable[[str], None] | None = None,
        writing: str = "",
    ) -> None:
        """Create the router.

        Args:
            rules: Decides whether a message needs the web.
            tool: The tool that answers such messages.
            report: Told what is happening, so the wait for the answer is not a bare spinner.
            writing: Said while the model writes the answer from the evidence.
        """
        super().__init__()
        self._rules = rules
        self._tool = tool
        self._report = report or (lambda _text: None)
        self._writing = writing

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """Call the research tool for the user's message if it needs the web, else carry on."""
        last = request.messages[-1] if request.messages else None
        # A message that comes with new text from the user's screen is about that text; the model
        # may still call research itself if it needs to. The app and window alone change nothing:
        # the rules look at what the user wrote.
        written = ScreenBlock.remove(last.text) if last is not None else ""
        if (
            last is not None
            and last.type == "human"
            and not ScreenBlock.brings_text(last.text)
            and self._rules.needs_sources(written)
        ):
            call = {
                "name": self._tool,
                "args": {"question": written.strip()},
                "id": f"{self.ROUTED}{uuid.uuid4().hex}",
            }
            return AIMessage(content="", tool_calls=[call])
        if last is not None and last.type == "tool" and self.ROUTED in str(last.tool_call_id):
            self._report(self._writing)
            response = handler(request)
            if any(
                message.tool_calls for message in response.result if isinstance(message, AIMessage)
            ):
                return handler(request.override(tools=[]))
            return response
        return handler(request)
