"""Tzara, a personal agent built on LangChain's ``create_agent``."""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
import warnings
from collections.abc import Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core._api import LangChainBetaWarning
from langchain_core.messages import AIMessage, ToolMessage

from tzara.agent.activity import Activity
from tzara.agent.events import AgentEvent, AssistantDelta, RunCompleted
from tzara.agent.links import LinkGuard
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.agent.models import ModelFactory
from tzara.agent.personal import MemoryMiddleware
from tzara.agent.routing import QuestionRules, ResearchRouter
from tzara.config import TzaraConfig
from tzara.memory import PersonalMemory
from tzara.tools.memory import MemoryTools
from tzara.tools.system import SystemTools
from tzara.tools.web import WebTools

if TYPE_CHECKING:
    from langgraph.stream.run_stream import GraphRunStream

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Failure:
    """A run failed on its worker thread; the caller's thread raises ``error``."""

    error: Exception


class _CheckedReply:
    """Passes the reply through the link guard and keeps what was written and what was shown."""

    def __init__(self, guard: LinkGuard, started: float) -> None:
        """Start checking a reply.

        Args:
            guard: Removes links the agent did not see.
            started: When the turn began, for the time to the first piece.
        """
        self._guard = guard
        self._started = started
        self._written: list[str] = []
        self._shown: list[str] = []
        self.first_token_after: float | None = None

    @property
    def written(self) -> str:
        """The reply as the model wrote it."""
        return "".join(self._written)

    @property
    def shown(self) -> str:
        """The reply as the user saw it, without links the agent did not see."""
        return "".join(self._shown)

    def process(self, item: AgentEvent) -> list[AgentEvent]:
        """The events to pass on for one event from the run."""
        if isinstance(item, AssistantDelta):
            self._written.append(item.text)
            return self._show(self._guard.feed(item.text))
        if isinstance(item, RunCompleted):
            events = self._show(self._guard.finish())
            return [*events, RunCompleted(self.first_token_after, len(self.shown))]
        return [item]

    def _show(self, text: str) -> list[AgentEvent]:
        """Pass on ``text`` if there is any, noting when the first of it appeared."""
        if not text:
            return []
        if self.first_token_after is None:
            self.first_token_after = time.monotonic() - self._started
        self._shown.append(text)
        return [AssistantDelta(text)]


class Tzara:
    """Conversational agent with web, memory and Mac tools.

    Wraps a LangChain agent graph and exposes what the CLI needs: streaming a reply,
    loading the model ahead of the first message, and cancelling a response.
    The conversation is remembered in a :class:`ConversationMemory`.
    """

    SUMMARIZE_AT = 0.85  # old turns are summarized once history is this much of the budget
    KEEP_AFTER_SUMMARY = 0.10  # and this much of the budget is kept as it was said
    REPLY_NODE = "model"  # the graph node whose output is the reply

    def __init__(
        self,
        model: str | None = None,
        config: TzaraConfig | None = None,
        memory: ConversationMemory | None = None,
        personal: PersonalMemory | None = None,
    ) -> None:
        """Create the agent.

        Args:
            model: Model as ``provider:model-name``. Defaults to ``config.model``.
            config: Configuration. Defaults to :class:`TzaraConfig` defaults.
            memory: Conversation to continue. Defaults to a new, empty one. Pass
                the previous agent's memory to keep the history across a model
                switch.
            personal: What Tzara knows about the user and has talked about before. Gives it
                the remember, forget and recall tools. ``None`` means it keeps nothing.
        """
        self._config = config or TzaraConfig()
        self._model = model or self._config.model
        self._memory = memory or ConversationMemory()
        self._factory = ModelFactory(self._config.ollama)
        self._activity = Activity(self._config.prompts)
        system_tools = SystemTools.create(self._config)
        self._system_rules = bool(system_tools)
        self._personal = personal
        web_tools = WebTools.create(
            self._config,
            report=self._activity.report,
            cancelled=lambda: self._cancel_requested,
        )
        memory_tools = MemoryTools.create(
            personal,
            self._config.prompts,
            blocked=lambda: self._activity.read_the_web,
            conversation_id=lambda: self._memory.conversation_id,
        )
        self._tools = [*web_tools, *system_tools, *memory_tools]
        self._stream: GraphRunStream | None = None
        self._cancel_requested = False

        # The v3 streaming protocol is flagged as beta, and langchain_core
        # re-enables the warning on import, so it would be printed into the chat.
        warnings.filterwarnings("ignore", category=LangChainBetaWarning)

        self._create_agent()
        self._restore_history()

    @property
    def model(self) -> str:
        """The model this agent uses, as ``provider:model-name``."""
        return self._model

    @property
    def memory(self) -> ConversationMemory:
        """The conversation this agent is part of."""
        return self._memory

    def run(self, text: str) -> Generator[AgentEvent]:
        """Answer one user message, as typed events in the order they happen.

        The message and reply are added to the conversation. Events from the model (pieces of
        the reply) and from tools (started, finished, progress) arrive through one queue, so a
        tool that takes thirty seconds is announced when it starts, not after it. The generator
        aborts the underlying run if it is closed early, and raises what the run raised if it
        failed.

        Args:
            text: The user's message.

        Yields:
            :class:`AssistantDelta`, :class:`ToolStarted`, :class:`ToolCompleted` and
            :class:`Progress` events as they happen, then :class:`RunCompleted`.
        """
        self._cancel_requested = False
        logger.info("turn started: %d characters", len(text))
        logger.debug("user message: %s", text)
        started = time.monotonic()
        outcome = "cancelled"

        inbox: queue.Queue[AgentEvent | _Failure | None] = queue.Queue()
        self._activity.begin_turn()
        self._activity.links.collect(text)  # an address the user typed is theirs to be repeated
        checked = _CheckedReply(LinkGuard(self._activity.links.trusts), started)
        self._activity.listen(inbox.put)
        stream = self._agent.stream_events(
            {"messages": [{"role": "user", "content": text}]},
            {**self._memory.run_config, "callbacks": [self._activity]},
            version="v3",
        )
        self._stream = stream
        self._memory.record("user", text)
        threading.Thread(
            target=self._pump,
            args=(stream, started, inbox),
            name="tzara-run",
            daemon=True,
        ).start()

        try:
            while (item := inbox.get()) is not None:
                if isinstance(item, _Failure):
                    raise item.error
                for event in checked.process(item):
                    yield event
                    if isinstance(event, RunCompleted):
                        outcome = "completed"
        except Exception:
            # Cancelling closes the connection, which surfaces as an error here.
            outcome = "cancelled" if self._cancel_requested else "failed"
            raise
        finally:
            self._activity.listen(None)
            if self._stream is stream:
                self._stream = None
            stream.abort()
            self._keep_only_seen_links(checked.written, checked.shown)
            self._drop_spent_evidence()
            self._memory.record("assistant", checked.shown)
            logger.info(
                "turn %s: first token after %s, total %.1fs, %d reply characters",
                outcome,
                "n/a" if checked.first_token_after is None else f"{checked.first_token_after:.1f}s",
                time.monotonic() - started,
                len(checked.shown),
            )

    def handle(self, text: str) -> Generator[str]:
        """Stream the text of the reply to one user message.

        This is :meth:`run` with only the pieces of the reply kept, for callers such as the
        command line that want just the words.

        Args:
            text: The user's message.

        Yields:
            Pieces of the assistant's reply as the model produces them.
        """
        with contextlib.closing(self.run(text)) as events:
            for event in events:
                if isinstance(event, AssistantDelta):
                    yield event.text

    @staticmethod
    def _pump(
        stream: GraphRunStream,
        started: float,
        inbox: queue.Queue[AgentEvent | _Failure | None],
    ) -> None:
        """Read the model's stream on a thread of its own and put what it says in ``inbox``.

        This is what lets tool events, which arrive from other threads, reach the caller while
        the model is silent. ``None`` ends the stream.
        """
        first_token_after: float | None = None
        characters = 0
        try:
            for message in stream.messages:
                if message.node != Tzara.REPLY_NODE:
                    # The summarizer runs in a node of its own; only the model's reply is shown.
                    logger.info("conversation history summarized to stay within the context window")
                    continue
                for piece in message.text:
                    if first_token_after is None:
                        first_token_after = time.monotonic() - started
                    characters += len(piece)
                    inbox.put(AssistantDelta(piece))
            inbox.put(RunCompleted(first_token_after, characters))
        except Exception as error:
            inbox.put(_Failure(error))
        finally:
            inbox.put(None)

    def history(self) -> list[HistoryMessage]:
        """Return the conversation so far, as the user and assistant said it.

        Tool calls, tool results and empty messages are left out.
        """
        state = self._agent.get_state(self._memory.run_config)
        messages = (state.values or {}).get("messages", [])
        roles = {"human": "user", "ai": "assistant"}
        return [
            HistoryMessage(roles[message.type], message.text)
            for message in messages
            if message.type in roles and message.text.strip()
        ]

    def warm_up(self) -> None:
        """Load the model and prime Ollama's prompt cache.

        Sends one throwaway message so the user's first real message does not
        pay for loading the model and evaluating the prompt. The message is not
        added to the conversation.

        Raises:
            Exception: Whatever the model client raises if it cannot be reached.
        """
        started = time.monotonic()
        with self._memory.scratch() as run_config:
            self._agent.invoke(
                {"messages": [{"role": "user", "content": self._config.warmup_message}]},
                run_config,
            )
        logger.info("model %s loaded in %.1fs", self._model, time.monotonic() - started)

    def cancel(self) -> None:
        """Cancel the in-flight model request.

        Model calls run in a worker thread blocked on the HTTP response. Closing
        the connection makes that read fail and stops generation. Without it,
        unwinding the stream or exiting the interpreter waits for the model to
        finish generating.

        Quick and non-blocking, so it is safe to call from a signal handler. The
        agent cannot be used again until :meth:`interrupt` rebuilds it.
        """
        self._cancel_requested = True
        self._factory.close_connection(self._chat_model)

    def interrupt(self) -> None:
        """Cancel the in-flight response and make the agent usable again.

        The cancelled client cannot be reused, so a fresh chat model and agent
        are built. The conversation is kept.
        """
        logger.info("response interrupted")
        self.cancel()

        stream, self._stream = self._stream, None
        if stream is not None:
            stream.abort()

        self._create_agent()

    def _middleware(self) -> list[Any]:
        """What runs around each model call: summarizing old turns, and the memory block."""
        budget = (self._chat_model.profile or {}).get(
            "max_input_tokens"
        ) or self._config.ollama.history_tokens
        middleware: list[Any] = [
            SummarizationMiddleware(
                self._chat_model,
                trigger=("tokens", int(budget * self.SUMMARIZE_AT)),
                keep=("tokens", int(budget * self.KEEP_AFTER_SUMMARY)),
            )
        ]
        if self._config.web.enabled:
            # First, so that a question that needs the web is sent to research before anything
            # else is added to the request.
            middleware.insert(
                0,
                ResearchRouter(
                    QuestionRules(self._config.research),
                    report=self._activity.report,
                    writing=self._config.prompts.router_writing,
                ),
            )
        if self._personal is not None:
            middleware.append(
                MemoryMiddleware(
                    self._personal.facts, self._personal.settings, self._config.prompts
                )
            )
        return middleware

    def _keep_only_seen_links(self, written: str, shown: str) -> None:
        """Correct the stored conversation when links were taken out of the reply.

        The user never saw them, so they must not come back when the chat is reopened or
        restored after a restart. Does nothing if nothing was taken out.
        """
        if written == shown:
            return
        try:
            state = self._agent.get_state(self._memory.run_config)
            last = next(
                message
                for message in reversed((state.values or {}).get("messages", []))
                if message.type == "ai" and message.text.strip()
            )
            self._agent.update_state(
                self._memory.run_config,
                {"messages": [AIMessage(content=shown, id=last.id)]},
                as_node="model",
            )
        except Exception:
            logger.warning("could not remove unseen links from the stored reply", exc_info=True)

    def _drop_spent_evidence(self) -> None:
        """Replace the research evidence in the stored conversation by a short note.

        The answer has been written from it. Left in, every later message makes the model read
        thousands of tokens of old search results again before it can start: "thanks" took
        sixteen seconds. The answer itself stays.
        """
        try:
            state = self._agent.get_state(self._memory.run_config)
            spent = [
                ToolMessage(
                    content=self._config.prompts.research_spent,
                    tool_call_id=message.tool_call_id,
                    id=message.id,
                    name=message.name,
                )
                for message in (state.values or {}).get("messages", [])
                if message.type == "tool"
                and message.name == "research"
                and message.text != self._config.prompts.research_spent
            ]
            if spent:
                self._agent.update_state(
                    self._memory.run_config, {"messages": spent}, as_node="tools"
                )
        except Exception:
            logger.warning("could not drop the spent search results", exc_info=True)

    def _restore_history(self) -> None:
        """Put back the conversation the memory found on disk, to carry on where it left off."""
        restored = self._memory.take_restore()
        if restored:
            self._agent.update_state(
                self._memory.run_config,
                {"messages": [{"role": m.role, "content": m.text} for m in restored]},
                as_node="model",
            )
            logger.info("restored %d messages of the last conversation", len(restored))

    def _create_agent(self) -> None:
        """Build the chat model and the agent graph around it."""
        self._cancel_requested = False
        self._chat_model = self._factory.create(self._model)
        self._agent = create_agent(
            model=self._chat_model,
            tools=self._tools,
            system_prompt=self._config.prompts.rendered_system(
                web=self._config.web.enabled,
                system=self._system_rules,
                memory=self._personal is not None,
            ),
            middleware=self._middleware(),
            checkpointer=self._memory.checkpointer,
        )
        logger.info("agent created with model %s", self._model)
