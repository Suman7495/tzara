"""Test doubles shared across the test suite."""

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field
from rich.console import Console


class RunawayLoop(BaseException):
    """Raised when code keeps reading input after the script ended.

    Derives from ``BaseException`` so a broad ``except Exception`` in the code
    under test cannot swallow it and spin forever.
    """


class ScriptedConsole(Console):
    """Console that reads scripted input and captures everything it prints.

    Each scripted item is either a line of text, or an exception (class or
    instance) to raise from ``input``. Running out of items raises ``EOFError``,
    like a closed stdin.
    """

    def __init__(self, script: Sequence[Any] = ()) -> None:
        super().__init__(file=io.StringIO(), force_terminal=False, width=120, color_system=None)
        self.script = list(script)
        self._reads_after_end = 0

    def input(self, prompt: Any = "", **_kwargs: Any) -> str:
        self.print(prompt, end="")
        if not self.script:
            self._reads_after_end += 1
            if self._reads_after_end > 3:
                raise RunawayLoop("input() kept being called after end of input")
            raise EOFError
        item = self.script.pop(0)
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        return str(item)

    @property
    def output(self) -> str:
        """Everything printed so far."""
        assert isinstance(self.file, io.StringIO)
        return self.file.getvalue()


class FakeAgent:
    """Stands in for :class:`tzara.agent.Tzara` in CLI tests.

    Attributes:
        created: Every instance created, in order.
        replies: Tokens ``handle`` yields.
        warm_up_error: Raised by ``warm_up`` when set.
        create_error: Raised by the constructor when set.
        handle_hook: Called with the message inside ``handle``; may raise.
    """

    created: ClassVar[list[FakeAgent]] = []
    replies: ClassVar[list[str]] = ["Hello", " there"]
    warm_up_error: ClassVar[BaseException | None] = None
    create_error: ClassVar[BaseException | None] = None
    handle_hook: ClassVar[Any] = None

    def __init__(self, model: str | None = None, config: Any = None, memory: Any = None) -> None:
        create_error = type(self).create_error
        if create_error is not None:
            raise create_error
        self.model = model or "ollama:fake"
        self.config = config
        self.memory = memory
        self.events: list[str] = []
        self.messages: list[str] = []
        type(self).created.append(self)

    @classmethod
    def reset(cls) -> type[FakeAgent]:
        """Return a fresh subclass so tests don't share class-level state."""
        return type("IsolatedFakeAgent", (cls,), {"created": [], "replies": ["Hello", " there"]})

    def handle(self, text: str) -> Iterator[str]:
        self.messages.append(text)
        if type(self).handle_hook is not None:
            type(self).handle_hook(text)
        yield from type(self).replies

    def warm_up(self) -> None:
        self.events.append("warm_up")
        warm_up_error = type(self).warm_up_error
        if warm_up_error is not None:
            raise warm_up_error

    def cancel(self) -> None:
        self.events.append("cancel")

    def interrupt(self) -> None:
        self.events.append("interrupt")


class RecordingFakeModel(GenericFakeChatModel):
    """Fake chat model that records the tools and messages it is given."""

    bound_tools: list[Any] = Field(default_factory=list)
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools: Any, **_kwargs: Any) -> RecordingFakeModel:
        self.bound_tools = list(tools)
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.seen_messages.append(messages)
        return super()._generate(messages, *args, **kwargs)

    def _stream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
        self.seen_messages.append(messages)
        return super()._stream(messages, *args, **kwargs)

    @classmethod
    def replying(cls, *texts: str, **fields: Any) -> RecordingFakeModel:
        """Build a model that gives each of ``texts`` as a reply, in order."""
        return cls(messages=iter([AIMessage(content=text) for text in texts]), **fields)


class PromptAwareFakeModel(RecordingFakeModel):
    """Fake model that answers ``answer N``, or writes a summary when asked to.

    Needs no script, so it copes with however many model calls DeepAgents makes
    in a turn. The summarization call is a single standalone prompt containing
    ``SESSION INTENT`` (a heading of DeepAgents' summarization prompt). Ordinary
    calls carry a system prompt and history, even when that history already
    holds a summary, so they get the next answer.
    """

    SUMMARY: ClassVar[str] = "## SESSION INTENT the summary text"

    answers_given: int = 0
    summaries_written: int = 0

    def _reply(self, messages: list[BaseMessage]) -> str:
        if len(messages) == 1 and "SESSION INTENT" in messages[0].text:
            self.summaries_written += 1
            return self.SUMMARY
        self.answers_given += 1
        return f"answer {self.answers_given}"

    def _generate(self, messages: list[BaseMessage], *_args: Any, **_kwargs: Any) -> ChatResult:
        self.seen_messages.append(messages)
        message = AIMessage(content=self._reply(messages))
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self, messages: list[BaseMessage], *_args: Any, **_kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        self.seen_messages.append(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(content=self._reply(messages)))
