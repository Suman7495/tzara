"""Test doubles shared across the test suite."""

from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Iterator, Sequence
from typing import Any, ClassVar

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolCallChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from rich.console import Console

from tzara.agent.events import (
    AgentEvent,
    AssistantDelta,
    RunCompleted,
    ToolCompleted,
    ToolStarted,
)


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

    def __init__(
        self,
        model: str | None = None,
        config: Any = None,
        memory: Any = None,
        personal: Any = None,
    ) -> None:
        create_error = type(self).create_error
        if create_error is not None:
            raise create_error
        self.model = model or "ollama:fake"
        self.config = config
        self.memory = memory
        self.personal = personal
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

    def cancel_suggestions(self) -> None:
        self.events.append("cancel_suggestions")

    def suggest(self) -> list[str]:
        return ["Tell me more", "Why?"]

    def interrupt(self) -> None:
        self.events.append("interrupt")


class StreamingFakeAgent:
    """Stands in for :class:`tzara.agent.Tzara` in server tests.

    Like the real agent it can be cancelled from another thread: ``cancel()``
    makes a reply in progress fail, as closing the model's connection does.

    Attributes:
        created: Every instance created, in order.
        tokens: Pieces ``handle`` yields.
        warm_up_error: Raised by ``warm_up`` when set.
        warm_up_gate: ``warm_up`` waits for this event when set.
        create_error: Raised by the constructor when set.
        reply_gate: A reply pauses after its first piece until this is set.
        reply_error: Raised partway through a reply when set.
        conversation: What ``history`` returns.
    """

    created: ClassVar[list[StreamingFakeAgent]] = []
    tokens: ClassVar[list[str]] = ["Hello", " ", "there"]
    warm_up_error: ClassVar[BaseException | None] = None
    warm_up_gate: ClassVar[threading.Event | None] = None
    create_error: ClassVar[BaseException | None] = None
    reply_gate: ClassVar[threading.Event | None] = None
    reply_error: ClassVar[BaseException | None] = None
    conversation: ClassVar[list[Any]] = []
    follow_ups: ClassVar[list[str]] = ["Tell me more", "Why?"]
    statuses: ClassVar[list[str]] = []

    def __init__(
        self,
        model: str | None = None,
        config: Any = None,
        memory: Any = None,
        personal: Any = None,
    ) -> None:
        create_error = type(self).create_error
        if create_error is not None:
            raise create_error
        self.model = model or "ollama:fake"
        self.config = config
        self.memory = memory
        self.personal = personal
        self.events: list[str] = []
        self.messages: list[str] = []
        self.cancelled = threading.Event()
        type(self).created.append(self)

    @classmethod
    def isolated(cls) -> type[StreamingFakeAgent]:
        """Return a fresh subclass so tests don't share class-level state."""
        return type(
            "IsolatedStreamingFakeAgent",
            (cls,),
            {
                "created": [],
                "tokens": list(cls.tokens),
                "conversation": [],
                "follow_ups": list(cls.follow_ups),
                "statuses": [],
            },
        )

    def history(self) -> list[Any]:
        return list(type(self).conversation)

    def suggest(self) -> list[str]:
        self.events.append("suggest")
        return list(type(self).follow_ups)

    def cancel_suggestions(self) -> None:
        self.events.append("cancel_suggestions")

    def warm_up(self) -> None:
        self.events.append("warm_up")
        gate = type(self).warm_up_gate
        if gate is not None:
            gate.wait(timeout=10)
        error = type(self).warm_up_error
        if error is not None:
            raise error

    def run(self, text: str) -> Iterator[AgentEvent]:
        """Typed events, as the real agent gives them: tools first, then the reply."""
        characters = 0
        for status in type(self).statuses:
            yield ToolStarted("fake_tool", status)
            yield ToolCompleted("fake_tool")
        for piece in self.handle(text):
            characters += len(piece)
            yield AssistantDelta(piece)
        yield RunCompleted(0.01, characters)

    def handle(self, text: str) -> Iterator[str]:
        self.messages.append(text)
        for position, token in enumerate(type(self).tokens):
            self._check_cancelled()
            gate = type(self).reply_gate
            if position == 1 and gate is not None:
                while not gate.is_set():
                    self._check_cancelled()
                    time.sleep(0.01)
            yield token
            reply_error = type(self).reply_error
            if position == 1 and reply_error is not None:
                raise reply_error

    def cancel(self) -> None:
        self.events.append("cancel")
        self.cancelled.set()

    def interrupt(self) -> None:
        self.events.append("interrupt")
        self.cancelled.clear()

    def _check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise ConnectionError("connection closed")


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


class ToolCallingFakeModel(RecordingFakeModel):
    """Fake model that calls one tool, then answers once it has the tool's result."""

    tool_name: str = "echo"
    tool_args: dict[str, Any] = Field(default_factory=lambda: {"text": "hi"})
    final_answer: str = "It is 4."

    def _generate(self, messages: list[BaseMessage], *_args: Any, **_kwargs: Any) -> ChatResult:
        self.seen_messages.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self, messages: list[BaseMessage], *_args: Any, **_kwargs: Any
    ) -> Iterator[ChatGenerationChunk]:
        self.seen_messages.append(messages)
        if any(message.type == "tool" for message in messages):
            yield ChatGenerationChunk(message=AIMessageChunk(content=self.final_answer))
            return
        call = ToolCallChunk(
            name=self.tool_name, args=json.dumps(self.tool_args), id="call-1", index=0
        )
        yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[call]))

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        if any(message.type == "tool" for message in messages):
            return AIMessage(content=self.final_answer)
        return AIMessage(
            content="",
            tool_calls=[{"name": self.tool_name, "args": self.tool_args, "id": "call-1"}],
        )


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


class FakeClock:
    """Time that only moves when something sleeps, so waiting costs nothing."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeRunner:
    """Stands in for running other programs: records each command, answers from a handler.

    ``handler`` receives the command and returns a :class:`CommandResult`, or ``None`` to
    use the default of success.
    """

    def __init__(self, handler: Any = None) -> None:
        self.calls: list[list[str]] = []
        self._handler = handler

    def run(self, args: Sequence[str]) -> Any:
        from tzara.installer.commands import CommandResult

        command = list(args)
        self.calls.append(command)
        result = self._handler(command) if self._handler else None
        return result or CommandResult(0)

    def commands(self) -> list[str]:
        """The first word of each command run, in order."""
        return [call[0] for call in self.calls]


def personal_memory(directory: Any = None, **settings: Any) -> Any:
    """A memory that is certainly on, in RAM or in ``directory``."""
    from dataclasses import replace

    from tzara.config import MemorySettings
    from tzara.memory import PersonalMemory

    memory = PersonalMemory.open(replace(MemorySettings(), **settings), directory)
    assert memory is not None
    return memory


class SideDispatcher:
    """What a helper request (one made at temperature 0) talks to in tests.

    The router asks the model what kind of every question this is, which in a real agent goes to
    a connection of its own. Here that question is answered at once, so that it does not use up a
    reply scripted for the real answer; any other helper request goes to the scripted model, as it
    always did.
    """

    TOPIC_QUESTION = "Decide what kind of question this is"

    def __init__(self, model: Any, topic: str = "general") -> None:
        self.model = model
        self.topic = topic

    def invoke(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        if self.TOPIC_QUESTION in str(prompt):
            return AIMessage(content=self.topic)
        return self.model.invoke(prompt, *args, **kwargs)


def fake_creator(model: Any, topic: str = "general") -> Any:
    """A stand-in for ``ModelFactory.create``: the scripted model, and a dispatcher for helpers."""
    side = SideDispatcher(model, topic)

    def create(_spec: str, temperature: float | None = None) -> Any:
        return model if temperature is None else side

    return create


class EchoTool(BaseTool):
    """A harmless tool for tests where the model has to call something."""

    name: str = "echo"
    description: str = "Repeat the text."
    args_schema: type[BaseModel] = type(
        "EchoArgs", (BaseModel,), {"__annotations__": {"text": str}}
    )

    def _run(self, text: str) -> str:
        return text
