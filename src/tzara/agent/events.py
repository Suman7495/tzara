"""What happens while Tzara answers, as typed events.

A client such as the macOS app wants more than the text of the reply: which tool is running,
whether it finished, how far a piece of research has got. :meth:`tzara.agent.Tzara.run` yields
these events in the order they happen; :meth:`tzara.agent.Tzara.handle` is the same stream with
only the text kept.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssistantDelta:
    """A piece of the reply, as the model writes it.

    Attributes:
        text: The next characters of the reply.
    """

    text: str


@dataclass(frozen=True)
class ToolStarted:
    """The agent has started using a tool.

    Attributes:
        name: The tool's name, such as ``web_search``.
        description: What it is doing, in a sentence for the user: ``Searching the web for …``.
    """

    name: str
    description: str


@dataclass(frozen=True)
class ToolCompleted:
    """A tool has finished.

    Attributes:
        name: The tool's name.
        failed: Whether it raised an error. A tool that returns an error as text, which the
            model then reads, still counts as completed.
    """

    name: str
    failed: bool = False


@dataclass(frozen=True)
class Progress:
    """A step inside a tool that takes several, such as one stage of research.

    Attributes:
        text: What is happening now, in a sentence for the user.
    """

    text: str


@dataclass(frozen=True)
class RunCompleted:
    """The reply is complete. The last event of a run that did not fail.

    Attributes:
        first_token_seconds: Time to the first piece of the reply, or ``None`` if there was none.
        characters: Length of the whole reply.
    """

    first_token_seconds: float | None
    characters: int


AgentEvent = AssistantDelta | ToolStarted | ToolCompleted | Progress | RunCompleted
