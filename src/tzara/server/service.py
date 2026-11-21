"""The agent behind the server: one conversation, one turn at a time."""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, replace

from tzara.agent import Tzara
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.config import TzaraConfig
from tzara.server.files import NameStore

logger = logging.getLogger(__name__)


class ServiceState(enum.Enum):
    """Whether the agent can answer yet."""

    NEW = "new"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


class ServiceNotReady(RuntimeError):
    """Raised when a request needs the agent before its model has loaded."""


class ServiceBusy(RuntimeError):
    """Raised when a request needs the agent while it is answering another."""


@dataclass(frozen=True)
class TokenEvent:
    """A piece of the reply."""

    text: str


@dataclass(frozen=True)
class DoneEvent:
    """The reply is complete.

    Attributes:
        first_token_seconds: Time to the first piece, or ``None`` if there was none.
        characters: Length of the whole reply.
    """

    first_token_seconds: float | None
    characters: int


@dataclass(frozen=True)
class ErrorEvent:
    """The reply failed."""

    message: str


TurnEvent = TokenEvent | DoneEvent | ErrorEvent

# What a turn reports to: each event, then ``None`` when nothing more will come.
EventSink = Callable[[TurnEvent | None], None]


class ChatTurn:
    """One question and its streamed answer, run on its own thread.

    Events go to a sink as they happen instead of being pulled from a generator.
    Cancelling from another thread, or a client vanishing mid-reply, therefore
    never has to close a generator that is still running.
    """

    def __init__(
        self, agent: Tzara, text: str, sink: EventSink, release: Callable[[], None]
    ) -> None:
        """Prepare a turn. Nothing runs until :meth:`start`.

        Args:
            agent: The agent to ask.
            text: The user's message.
            sink: Receives each event, then ``None`` at the end.
            release: Called once the turn is over, to free the service.
        """
        self._agent = agent
        self._text = text
        self._sink = sink
        self._release = release
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        """Whether :meth:`cancel` has been called."""
        return self._cancelled.is_set()

    def start(self) -> None:
        """Run the turn on a background thread."""
        threading.Thread(target=self._run, name="tzara-chat-turn", daemon=True).start()

    def cancel(self) -> None:
        """Stop the turn as soon as possible. Safe to call from any thread.

        Closing the model's connection makes a blocked read fail at once, instead
        of waiting for the next token. The agent is rebuilt afterwards, on the
        turn's own thread.
        """
        self._cancelled.set()
        self._agent.cancel()

    def _run(self) -> None:
        """Stream the reply into the sink, whatever happens."""
        started = time.monotonic()
        first_token_after: float | None = None
        characters = 0
        replies = self._agent.handle(self._text)
        try:
            for piece in replies:
                if self.cancelled:
                    break
                if first_token_after is None:
                    first_token_after = time.monotonic() - started
                characters += len(piece)
                self._sink(TokenEvent(piece))
            if not self.cancelled:
                self._sink(DoneEvent(first_token_after, characters))
        except Exception as error:  # pylint: disable=broad-exception-caught
            if self.cancelled:
                logger.info("chat turn cancelled: %s", error)
            else:
                logger.exception("chat turn failed")
                self._sink(ErrorEvent(str(error)))
        finally:
            self._finish(replies)

    def _finish(self, replies: Generator[str]) -> None:
        """Clean up after the turn and free the service."""
        try:
            replies.close()
            if self.cancelled:
                self._agent.interrupt()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("could not reset the agent after a cancelled turn")
        finally:
            self._release()
            self._sink(None)


class ChatService:
    """Owns the agent and lets one request use it at a time.

    The model loads in the background so the server can answer health checks at
    once. Requests that need the agent are refused, rather than queued, while
    it is loading or busy: a client can show that state, a silent wait it cannot.
    """

    def __init__(
        self,
        config: TzaraConfig,
        agent_factory: Callable[..., Tzara] = Tzara,
        memory: ConversationMemory | None = None,
        name_store: NameStore | None = None,
    ) -> None:
        """Create a service. The agent is built by :meth:`start`.

        Args:
            config: Configuration.
            agent_factory: Builds an agent from ``model``, ``config`` and
                ``memory`` keyword arguments. Replaced in tests.
            memory: Conversation shared by every agent this service builds, so it
                survives a model switch. Defaults to a new, empty one.
            name_store: Where to remember the assistant's name. ``None`` forgets it
                when the service stops.
        """
        self._config = config
        self._agent_factory = agent_factory
        self._memory = memory or ConversationMemory()
        self._name_store = name_store
        self._lock = threading.Lock()
        self._state = ServiceState.NEW
        self._error: str | None = None
        self._agent: Tzara | None = None
        self._turn: ChatTurn | None = None

    @property
    def state(self) -> ServiceState:
        """Whether the agent can answer yet."""
        return self._state

    @property
    def error(self) -> str | None:
        """Why loading failed, when the state is ``FAILED``."""
        return self._error

    @property
    def model(self) -> str:
        """The model in use, as ``provider:model-name``."""
        return self._agent.model if self._agent else self._config.model

    @property
    def name(self) -> str:
        """What the assistant calls itself."""
        return self._config.prompts.assistant_name

    @property
    def busy(self) -> bool:
        """Whether a reply is being written."""
        return self._turn is not None

    def start(self) -> None:
        """Build the agent and load its model. Blocks until done.

        Meant to run on a background thread. A failure is recorded, not raised:
        it shows up in :attr:`state` and :attr:`error`, and a later
        :meth:`switch_model` can retry.
        """
        with self._lock:
            self._state = ServiceState.LOADING
            try:
                self._agent = self._agent_factory(
                    model=self._config.model, config=self._config, memory=self._memory
                )
                self._agent.warm_up()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.exception("could not load the model")
                self._state = ServiceState.FAILED
                self._error = str(error)
            else:
                self._state = ServiceState.READY
                self._error = None

    def start_turn(self, text: str, sink: EventSink) -> ChatTurn:
        """Begin answering ``text``.

        Args:
            text: The user's message.
            sink: Receives each event as it happens, then ``None``.

        Returns:
            The running turn, which can be cancelled.

        Raises:
            ServiceNotReady: If the model has not loaded.
            ServiceBusy: If another reply is being written.
        """
        if self._state is not ServiceState.READY or self._agent is None:
            raise ServiceNotReady(f"the model is not ready (state: {self._state.value})")
        if not self._lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            raise ServiceBusy("another reply is still being written")
        try:
            turn = ChatTurn(self._agent, text, sink, release=self._end_turn)
            self._turn = turn
            turn.start()
        except BaseException:
            self._turn = None
            self._lock.release()
            raise
        return turn

    def interrupt(self) -> bool:
        """Cancel the reply being written, if any.

        Returns:
            True if a reply was cancelled.
        """
        turn = self._turn
        if turn is None:
            return False
        turn.cancel()
        return True

    def new_conversation(self) -> None:
        """Forget the conversation so far.

        Raises:
            ServiceBusy: If the agent is answering or loading.
        """
        if not self._lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            raise ServiceBusy("the agent is busy")
        try:
            self._memory.reset()
            logger.info("new conversation started")
        finally:
            self._lock.release()

    def switch_model(self, model: str) -> None:
        """Load another model and make it the one in use.

        The conversation carries over. If loading fails, the previous model stays
        in use.

        Args:
            model: New model as ``provider:model-name``.

        Raises:
            ServiceBusy: If the agent is answering or loading.
            Exception: Whatever building or loading the new model raises.
        """
        self._replace_agent(model, self._config)
        logger.info("switched to model %s", model)

    def rename(self, name: str) -> None:
        """Give the assistant a new name, which it will use to describe itself.

        The name is part of the system prompt, so the agent is rebuilt and loaded
        again. The conversation carries over. If that fails, the old name stays.

        Args:
            name: The new name. Surrounding whitespace is ignored.

        Raises:
            ValueError: If the name is empty, too long, or spans several lines.
            ServiceBusy: If the agent is answering or loading.
            Exception: Whatever building or loading the agent raises.
        """
        cleaned = name.strip()
        limit = self._config.server.max_name_chars
        if not cleaned or len(cleaned) > limit or len(cleaned.splitlines()) > 1:
            raise ValueError(f"the name must be one line of 1 to {limit} characters")
        if cleaned == self.name:
            return

        prompts = replace(self._config.prompts, assistant_name=cleaned)
        self._replace_agent(self.model, replace(self._config, prompts=prompts))
        if self._name_store is not None:
            self._name_store.save(cleaned)
        logger.info("assistant renamed")

    def history(self) -> list[HistoryMessage]:
        """Return the conversation so far. Empty before the model has loaded."""
        return self._agent.history() if self._agent else []

    def _replace_agent(self, model: str, config: TzaraConfig) -> None:
        """Build, load and swap in a new agent, keeping the old one if that fails.

        Raises:
            ServiceBusy: If the agent is answering or loading.
            Exception: Whatever building or loading the new agent raises.
        """
        if not self._lock.acquire(blocking=False):  # pylint: disable=consider-using-with
            raise ServiceBusy("the agent is busy")
        previous_state = self._state
        try:
            self._state = ServiceState.LOADING
            candidate = self._agent_factory(model=model, config=config, memory=self._memory)
            candidate.warm_up()
        except BaseException:
            self._state = previous_state
            raise
        else:
            self._agent = candidate
            self._config = config
            self._state = ServiceState.READY
            self._error = None
        finally:
            self._lock.release()

    def close(self) -> None:
        """Stop whatever the agent is doing, so the process can exit.

        Cancels the reply being written, and a model that is still loading.
        """
        self.interrupt()
        if self._state is ServiceState.LOADING and self._agent is not None:
            self._agent.cancel()

    def _end_turn(self) -> None:
        """Free the service once a turn is over."""
        self._turn = None
        self._lock.release()
