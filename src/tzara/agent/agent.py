"""Tzara, a personal agent built on LangChain's ``create_agent``."""

from __future__ import annotations

import contextlib
import logging
import queue
import re
import threading
import time
import warnings
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core._api import LangChainBetaWarning
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tzara.agent.actions import ActionNote
from tzara.agent.activity import Activity
from tzara.agent.events import (
    AgentEvent,
    AssistantDelta,
    Choice,
    Choices,
    Progress,
    RunCompleted,
    ToolCompleted,
    ToolStarted,
)
from tzara.agent.links import LinkGuard
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.agent.models import ModelFactory
from tzara.agent.personal import MemoryMiddleware
from tzara.agent.routing import (
    AppRouter,
    EmptyReplyRetry,
    PromiseRetry,
    QuestionRules,
    ResearchRouter,
)
from tzara.agent.screen import Route, ScreenBlock, ScreenContext, ScreenPage
from tzara.agent.writer import InlineWriter, WriteRequest, WriteResult
from tzara.config import TzaraConfig
from tzara.mcp import McpHub, McpTool, McpTools
from tzara.memory import PersonalMemory
from tzara.tools.memory import MemoryTools
from tzara.tools.system import SystemTools
from tzara.tools.web import PageReader, WebTools

if TYPE_CHECKING:
    from langgraph.stream.run_stream import GraphRunStream

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Failure:
    """A run failed on its worker thread; the caller's thread raises ``error``."""

    error: Exception


class _CheckedReply:
    """Passes the reply through the link guard and keeps what was written and what was shown."""

    def __init__(
        self, guard: LinkGuard, started: float, closing_note: Callable[[str], str]
    ) -> None:
        """Start checking a reply.

        Args:
            guard: Removes links the agent did not see.
            started: When the turn began, for the time to the first piece.
            closing_note: Given the finished reply, returns text to add at its end (or nothing).
        """
        self._closing_note = closing_note
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
            events += self._show(self._closing_note(self.written))
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
        mcp: McpHub | None = None,
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
            mcp: The connected apps, whose tools it is given beside its own. ``None`` means none.
        """
        self._config = config or TzaraConfig()
        self._model = model or self._config.model
        self._memory = memory or ConversationMemory()
        self._factory = ModelFactory(self._config.ollama)
        self._activity = Activity(self._config.prompts)
        system_tools = SystemTools.create(self._config)
        self._system_rules = bool(system_tools)
        self._personal = personal
        reader = PageReader(self._config.web, self._config.prompts)
        web_tools = WebTools.create(
            self._config,
            report=self._activity.report,
            cancelled=lambda: self._cancel_requested,
            reader=reader,
        )
        self._screen_page = ScreenPage(
            reader if self._config.web.enabled else None, self._config.screen
        )
        memory_tools = MemoryTools.create(
            personal,
            self._config.prompts,
            blocked=lambda: self._activity.read_the_web,
            conversation_id=lambda: self._memory.conversation_id,
        )
        app_tools = (
            McpTools.create(mcp, mcp.servers, self._config.mcp, self._config.prompts) if mcp else []
        )
        self._app_rules = bool(app_tools)
        self._tools = [*web_tools, *system_tools, *memory_tools, *app_tools]
        self._app_names = sorted({tool.server for tool in app_tools if isinstance(tool, McpTool)})
        self._stream: GraphRunStream | None = None
        self._cancel_requested = False
        # Set when the model's connection has been closed to stop a reply: it cannot be used again.
        self._connection_closed = False
        self._last_selection = ""
        self._selection_conversation: int | None = None
        # What to do with this turn's message if it was decided before the model is asked:
        # "skip" (no web), ("search", question), or None (the rules decide).
        self._turn_route: str | tuple[str, str] | None = None
        # The connected app and address of the page the user has open, when this turn's message
        # is about it: the app router reads it first.
        self._turn_open_page: tuple[str, str] | None = None
        self._question_rules = QuestionRules(self._config.research)

        # The v3 streaming protocol is flagged as beta, and langchain_core
        # re-enables the warning on import, so it would be printed into the chat.
        warnings.filterwarnings("ignore", category=LangChainBetaWarning)

        self._writer = InlineWriter(lambda: self._chat_model, self._config, personal)
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

    def run(
        self,
        text: str,
        context: ScreenContext | None = None,
        search_for: str | None = None,
    ) -> Generator[AgentEvent]:
        """Answer one user message, as typed events in the order they happen.

        The message and reply are added to the conversation. Events from the model (pieces of
        the reply) and from tools (started, finished, progress) arrive through one queue, so a
        tool that takes thirty seconds is announced when it starts, not after it. The generator
        aborts the underlying run if it is closed early, and raises what the run raised if it
        failed.

        Args:
            text: The user's message.
            context: What the user was looking at, to be put in front of the message for the
                model. It is not part of what the user said: history shows ``text`` alone.
            search_for: A question to search the web for whatever the message says. It is what
                the "Search online" button sends: the message is only its label.

        Yields:
            :class:`AssistantDelta`, :class:`ToolStarted`, :class:`ToolCompleted` and
            :class:`Progress` events as they happen, then :class:`RunCompleted`.
        """
        self._reopen_if_closed()
        self._cancel_requested = False
        self._turn_route = ("search", search_for) if search_for else None
        self._turn_open_page = None
        logger.info("turn started: %d characters", len(text))
        logger.debug("user message: %s", text)
        started = time.monotonic()
        outcome = "cancelled"

        inbox: queue.Queue[AgentEvent | _Failure | None] = queue.Queue()
        self._activity.begin_turn()
        shown_to_model = text
        if context is not None and not context.empty and self._config.screen.enabled:
            page, page_note = "", ""
            open_in = self._app_open(context)
            if open_in and self._about_open_page(text, context):
                self._turn_open_page = (open_in, context.address)
                if search_for is None:
                    self._turn_route = "skip"  # about the open page, not the web
            if not open_in and self._screen_page.wanted(text, context):
                yield Progress(self._config.prompts.screen_reading)
                page, page_note = self._screen_page.read(context)
            unchanged = self._selection_seen(context)
            typing = visible = ""
            route = Route.DEFAULT
            if search_for is None:
                route = self._screen_page.route(
                    text, context, self._question_rules.needs_sources(text), bool(page)
                )
            if route is Route.ASK:
                yield from self._ask(text)
                return
            if route is Route.SCREEN:
                self._turn_route = "skip"
            elif route is Route.WEB:
                self._turn_route = ("search", text)
            if route is Route.SCREEN or self._screen_page.about_the_screen(text, context):
                typing = context.typing
                # The window's text is for when nothing more specific is meant.
                if not page and not context.selection:
                    visible = context.visible
            shown_to_model = ScreenBlock.wrap(
                context,
                text,
                self._config.screen,
                page,
                page_note,
                unchanged,
                typing,
                visible,
                open_in,
            )
            logger.info(
                "screen context: app=%s, address host=%s, window %d chars, route=%s, "
                "selection %d, typing %d, visible %d, page %d chars; open page of a connected "
                "app: %s",
                context.app,
                urlparse(context.address).hostname or ("path" if context.address else "none"),
                len(context.window),
                route.name,
                len(context.selection),
                len(typing),
                len(visible),
                len(page),
                open_in or "no",
            )
            if ScreenBlock.brings_text(shown_to_model):
                # New text from the screen may hold instructions: nothing is remembered on its say.
                self._activity.distrust()
        else:
            logger.info("screen context: none sent")
        # An address the user typed, or had selected, is theirs to be repeated.
        self._activity.links.collect(shown_to_model)
        checked = _CheckedReply(LinkGuard(self._activity.links.trusts), started, self._closing_note)
        self._activity.listen(inbox.put)
        if self._personal is not None and search_for is None:
            for fact in self._personal.capture.learn(text):
                yield Progress(self._config.prompts.learned.format(fact=fact.text))
        stream = self._agent.stream_events(
            {"messages": [{"role": "user", "content": shown_to_model}]},
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
            for item in self._events(inbox):
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
            self._remember_stand_in(checked.written, checked.shown)
            self._keep_only_seen_links(checked.written, checked.shown)
            self._drop_spent_evidence()
            note = self._remember_actions()
            self._memory.record("assistant", checked.shown + note)
            logger.info(
                "turn %s: tools run %s; first token after %s, total %.1fs, %d reply characters",
                outcome,
                sorted(self._activity.tools_run) or "none",
                "n/a" if checked.first_token_after is None else f"{checked.first_token_after:.1f}s",
                time.monotonic() - started,
                len(checked.shown),
            )

    def _events(
        self, inbox: queue.Queue[AgentEvent | _Failure | None]
    ) -> Generator[AgentEvent | _Failure]:
        """What arrives in ``inbox``, with a note on what is going on whenever it is silent.

        Reading a long prompt takes many seconds on a local model; the user is told so, in turn,
        until the reply starts to be written.
        """
        prompts = self._config.prompts
        marks = self._config.waiting_seconds
        last, noted, after_tool, writing = time.monotonic(), 0, False, False
        while True:
            timeout = None
            if not writing and noted < len(marks):
                timeout = max(0.05, marks[noted] - (time.monotonic() - last))
            try:
                item = inbox.get(timeout=timeout)
            except queue.Empty:
                texts = (
                    prompts.waiting_result if after_tool else prompts.waiting_reading,
                    prompts.waiting_still,
                    prompts.waiting_long,
                )
                yield Progress(texts[min(noted, len(texts) - 1)])
                noted += 1
                last = time.monotonic()
                continue
            if item is None:
                return
            last, noted = time.monotonic(), 0
            writing = writing or isinstance(item, AssistantDelta)
            after_tool = isinstance(item, ToolCompleted) or (
                after_tool and not isinstance(item, ToolStarted)
            )
            yield item

    def _closing_note(self, reply: str) -> str:
        """What to add at the end of a finished reply: a stand-in for none, or a warning.

        A reply the model left empty is replaced by a plain sentence, so nothing silently
        vanishes. A reply that claims a change in an app is warned about by
        :meth:`_unconfirmed_change`.
        """
        if not reply.strip():
            logger.warning("the reply was empty: showing the stand-in sentence")
            return self._config.prompts.empty_reply
        note = self._unconfirmed_change(reply)
        if note:
            logger.warning("the reply claims a change but no tool ran: warning added")
        return note

    def _unconfirmed_change(self, reply: str) -> str:
        """A note for a reply that says something was changed in an app when no app tool ran.

        A small model reports success it never had. Decided here, in code, from the words of the
        reply and the tools that ran: no model request. Left alone if any tool ran (such as
        ``remember``), since "I've added that" may then be true.
        """
        if not self._app_rules or self._activity.tools_run:
            return ""
        if re.search(self._config.mcp.claim_pattern, reply, re.IGNORECASE):
            return self._config.prompts.unconfirmed_change
        return ""

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
            HistoryMessage(roles[message.type], ActionNote.strip(ScreenBlock.remove(message.text)))
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
        self._reopen_if_closed()
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
        self._connection_closed = True
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

    def _ask(self, text: str) -> Generator[AgentEvent]:
        """Ask whether to search online, for a question that seems unrelated to the screen.

        A search takes many seconds, so it is not started on a guess. The question and this
        reply are kept in the conversation, so the buttons that follow have something to refer to.
        """
        prompts = self._config.prompts
        ask = prompts.screen_ask
        started = time.monotonic()
        logger.info("asking whether to search online")
        yield AssistantDelta(ask)
        yield Choices(
            (
                Choice(prompts.choice_search_label, prompts.choice_search_message, search_for=text),
                Choice(prompts.choice_screen_label, prompts.choice_screen_message),
            )
        )
        self._agent.update_state(
            self._memory.run_config,
            {"messages": [HumanMessage(content=text), AIMessage(content=ask)]},
            as_node="model",
        )
        self._memory.record("user", text)
        self._memory.record("assistant", ask)
        yield RunCompleted(time.monotonic() - started, len(ask))

    def _app_open(self, context: ScreenContext) -> str:
        """The connected app whose own page the user has open, or empty.

        Found by the app's name in the address's host (``notion`` in ``app.notion.com``): then
        the address is the page to act on, and the model is told so.
        """
        host = (urlparse(context.address).hostname or "").lower()
        for app in self._app_names:
            words = [word for word in app.lower().split("-") if len(word) >= 4]
            if any(word in host for word in words):
                return app
        return ""

    def write(self, request: WriteRequest) -> WriteResult:
        """Write text for the user to put into a field of another app.

        One model request, no tools: see :class:`InlineWriter`.

        Raises:
            ValueError: If the model wrote nothing.
        """
        self._reopen_if_closed()
        return self._writer.write(request)

    def _about_open_page(self, text: str, context: ScreenContext) -> bool:
        """Whether the message is about the page open in a connected app (not small talk)."""
        words = set(re.findall(r"[a-z]+", text.lower()))
        if self._screen_page.asks_for_the_web(text):
            return False
        stems = self._config.screen.app_page_words
        return self._screen_page.about_the_screen(text, context) or any(
            word == stem or (len(stem) >= 5 and word.startswith(stem))
            for word in words
            for stem in stems
        )

    def _remember_actions(self) -> str:
        """Add what the reply did (which tool, on what, with what result) to the stored reply.

        The model's memory of a past reply is its words only; after "yes" or "expand it" it
        must know what was done and whether it worked. Returns the note written, if any.
        """
        records = list(self._activity.actions)
        note = ActionNote.render(records)
        if not note:
            return ""
        if self._personal is not None:
            for record in records:
                if record.tool not in ActionNote.QUIET:
                    where = f" on {record.target}" if record.target else ""
                    self._personal.activity.record("app", f"{record.tool}{where}: {record.result}")
        try:
            state = self._agent.get_state(self._memory.run_config)
            last = next(
                message
                for message in reversed((state.values or {}).get("messages", []))
                if message.type == "ai" and message.text.strip()
            )
            self._agent.update_state(
                self._memory.run_config,
                {"messages": [AIMessage(content=last.text + note, id=last.id)]},
                as_node="model",
            )
            logger.info("noted in the conversation: %s", note.strip())
        except Exception:
            logger.warning("could not note the reply's actions", exc_info=True)
            return ""
        return note

    def _selection_seen(self, context: ScreenContext) -> bool:
        """Whether this selection was already given to the model in this conversation.

        The context goes with every message, and a selection can stay for many of them: it is
        given once, then described as unchanged.
        """
        conversation = self._memory.conversation_id
        if conversation != self._selection_conversation:
            self._selection_conversation, self._last_selection = conversation, ""
        seen = bool(context.selection) and context.selection == self._last_selection
        self._last_selection = context.selection
        return seen

    def _reopen_if_closed(self) -> None:
        """Build a fresh model connection if the last one was closed to stop a reply.

        :meth:`interrupt` does this, but a stop that arrives while a reply is finishing can close
        the connection without it, and then every later message failed with "Cannot send a
        request, as the client has been closed" until the server restarted.
        """
        # Checked directly as well as noted by cancel(): whatever closed it, the next message must
        # not be left to fail with it. Restarting the app does not help, as the server keeps it.
        if self._connection_closed or self._factory.is_closed(self._chat_model):
            logger.info("the model connection was closed; opening a new one")
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
                    self._question_rules,
                    report=self._activity.report,
                    writing=self._config.prompts.router_writing,
                    route=lambda: self._turn_route,
                ),
            )
        searches = self._app_searches()
        fetches = self._app_tools_named(AppRouter.FETCH, ("id", "url", "page_id"))
        if searches or fetches:
            router = AppRouter(searches, fetches, lambda: self._turn_open_page)
            middleware.insert(1 if self._config.web.enabled else 0, router)
        middleware.append(EmptyReplyRetry())
        if self._app_rules:
            middleware.append(
                PromiseRetry(self._config.mcp.promise_pattern, self._config.prompts.act_now)
            )
        if self._personal is not None:
            middleware.append(
                MemoryMiddleware(
                    self._personal.facts,
                    self._personal.settings,
                    self._config.prompts,
                    self._personal.activity,
                )
            )
        return middleware

    def _app_searches(self) -> dict[str, tuple[str, str]]:
        """For each connected app that has a search tool among the ones given, how to call it."""
        found: dict[str, tuple[str, str]] = {}
        for tool in self._tools:
            if not isinstance(tool, McpTool) or tool.server in found:
                continue
            if not AppRouter.SEARCH.search(tool.remote_name):
                continue
            properties = tool.arguments.get("properties", {})
            argument = "query" if "query" in properties else next(iter(properties), "")
            if argument:
                found[tool.server] = (tool.name, argument)
        return found

    def _app_tools_named(
        self, pattern: re.Pattern[str], arguments: tuple[str, ...]
    ) -> dict[str, tuple[str, str]]:
        """Each connected app's tool named like ``pattern``, and its argument for an address."""
        found: dict[str, tuple[str, str]] = {}
        for tool in self._tools:
            if not isinstance(tool, McpTool) or tool.server in found:
                continue
            if not pattern.search(tool.remote_name):
                continue
            properties = tool.arguments.get("properties", {})
            argument = next((name for name in arguments if name in properties), "")
            if argument:
                found[tool.server] = (tool.name, argument)
        return found

    def _remember_stand_in(self, written: str, shown: str) -> None:
        """Store the sentence shown in place of an empty reply, so a reopened chat keeps it."""
        if written.strip() or not shown.strip():
            return
        try:
            self._agent.update_state(
                self._memory.run_config, {"messages": [AIMessage(content=shown)]}, as_node="model"
            )
        except Exception:
            logger.warning("could not store the stand-in for an empty reply", exc_info=True)

    def _keep_only_seen_links(self, written: str, shown: str) -> None:
        """Correct the stored conversation when links were taken out of the reply.

        The user never saw them, so they must not come back when the chat is reopened or
        restored after a restart. Does nothing if nothing was taken out.
        """
        if written == shown or not written.strip():
            return
        logger.info("links the agent never saw were taken out of the reply")
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
        sixteen seconds. The answer itself stays. So does the text of a page that came with a
        message about "this page", for the same reason.
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
            spent += [
                ToolMessage(
                    content=self._config.prompts.app_spent,
                    tool_call_id=message.tool_call_id,
                    id=message.id,
                    name=message.name,
                )
                for message in (state.values or {}).get("messages", [])
                if message.type == "tool"
                and str(message.tool_call_id).startswith(AppRouter.ROUTED)
                and message.text != self._config.prompts.app_spent
            ]
            if spent:
                self._agent.update_state(
                    self._memory.run_config, {"messages": spent}, as_node="tools"
                )
            # The same for the text of a page that came with a message.
            pages = [
                HumanMessage(content=ScreenBlock.forget_page(message.text), id=message.id)
                for message in (state.values or {}).get("messages", [])
                if message.type == "human" and ScreenBlock.forget_page(message.text) != message.text
            ]
            if pages:
                self._agent.update_state(
                    self._memory.run_config, {"messages": pages}, as_node="model"
                )
        except Exception:
            logger.warning("could not drop the spent search results", exc_info=True)

    def resume_history(self) -> None:
        """Put the conversation the memory has just switched to into the agent's checkpoint."""
        self._restore_history()

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
        self._connection_closed = False
        self._chat_model = self._factory.create(self._model)
        self._agent = create_agent(
            model=self._chat_model,
            tools=self._tools,
            system_prompt=self._config.prompts.rendered_system(
                web=self._config.web.enabled,
                system=self._system_rules,
                memory=self._personal is not None,
                screen=self._config.screen.enabled,
                apps=self._app_rules,
            ),
            middleware=self._middleware(),
            checkpointer=self._memory.checkpointer,
        )
        logger.info("agent created with model %s", self._model)
