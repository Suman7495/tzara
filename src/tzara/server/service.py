"""The agent behind the server: one conversation, one turn at a time."""

from __future__ import annotations

import enum
import logging
import shutil
import threading
import time
import webbrowser
from collections.abc import Callable, Generator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tzara.agent import Tzara
from tzara.agent.events import (
    AgentEvent,
    AssistantDelta,
    Choices,
    Progress,
    RunCompleted,
    ToolCompleted,
    ToolStarted,
)
from tzara.agent.memory import ConversationMemory, HistoryMessage
from tzara.agent.screen import ScreenContext
from tzara.agent.writer import WriteRequest, WriteResult
from tzara.config import TzaraConfig
from tzara.mcp import McpConfigFile, McpHub, McpPresets, McpServerConfig, McpTools
from tzara.mcp.oauth import TokenFile
from tzara.mcp.signin import SignIn, SignInState
from tzara.memory import PersonalMemory
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
class StatusEvent:
    """What the agent is doing right now, such as searching the web.

    Attributes:
        text: A short sentence for the user, such as ``Reading bbc.com…``.
    """

    text: str


@dataclass(frozen=True)
class ToolEvent:
    """A tool started or finished, for a client that shows what is running.

    Attributes:
        name: The tool's name, such as ``web_search``.
        state: ``started``, ``completed`` or ``failed``.
    """

    name: str
    state: str


@dataclass(frozen=True)
class ChoicesEvent:
    """Buttons to show under the reply, for a question the reply asks.

    Attributes:
        options: Each button as ``label``, ``message`` and, for one that searches, ``search_for``.
    """

    options: list[dict[str, str | None]]


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


TurnEvent = TokenEvent | StatusEvent | ToolEvent | ChoicesEvent | DoneEvent | ErrorEvent

# What a turn reports to: each event, then ``None`` when nothing more will come.
EventSink = Callable[[TurnEvent | None], None]


class ChatTurn:
    """One question and its streamed answer, run on its own thread.

    Events go to a sink as they happen instead of being pulled from a generator.
    Cancelling from another thread, or a client vanishing mid-reply, therefore
    never has to close a generator that is still running.
    """

    def __init__(
        self,
        agent: Tzara,
        text: str,
        sink: EventSink,
        release: Callable[[], None],
        context: ScreenContext | None = None,
        search_for: str | None = None,
    ) -> None:
        """Prepare a turn. Nothing runs until :meth:`start`.

        Args:
            agent: The agent to ask.
            text: The user's message.
            sink: Receives each event, then ``None`` at the end.
            release: Called once the turn is over, to free the service.
            context: What the user was looking at, if the app sent it.
            search_for: A question to search the web for, if the user pressed "Search online".
        """
        self._agent = agent
        self._text = text
        self._sink = sink
        self._release = release
        self._context = context
        self._search_for = search_for
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
        replies = self._agent.run(self._text, self._context, self._search_for)
        try:
            for event in replies:
                if self.cancelled:
                    break
                for turn_event in self._translate(event):
                    self._sink(turn_event)
        except Exception as error:
            if self.cancelled:
                logger.info("chat turn cancelled: %s", error)
            else:
                logger.exception("chat turn failed")
                self._sink(ErrorEvent(str(error)))
        finally:
            self._finish(replies)

    @staticmethod
    def _translate(event: AgentEvent) -> list[TurnEvent]:
        """The server events for one thing the agent did.

        A tool starting gives two: its name and state for a client that tracks tools, and a
        sentence for one that only wants to show something to the user.
        """
        if isinstance(event, AssistantDelta):
            return [TokenEvent(event.text)]
        if isinstance(event, ToolStarted):
            return [ToolEvent(event.name, "started"), StatusEvent(event.description)]
        if isinstance(event, ToolCompleted):
            return [ToolEvent(event.name, "failed" if event.failed else "completed")]
        if isinstance(event, Progress):
            return [StatusEvent(event.text)]
        if isinstance(event, Choices):
            return [
                ChoicesEvent(
                    [
                        {
                            "label": option.label,
                            "message": option.message,
                            "search_for": option.search_for,
                        }
                        for option in event.options
                    ]
                )
            ]
        assert isinstance(event, RunCompleted)
        return [DoneEvent(event.first_token_seconds, event.characters)]

    def _finish(self, replies: Generator[AgentEvent]) -> None:
        """Clean up after the turn and free the service."""
        try:
            replies.close()
            if self.cancelled:
                self._agent.interrupt()
        except Exception:
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

    AUTH_NAME = "mcp-auth.json"

    def __init__(
        self,
        config: TzaraConfig,
        agent_factory: Callable[..., Tzara] = Tzara,
        memory: ConversationMemory | None = None,
        name_store: NameStore | None = None,
        *,
        personal: PersonalMemory | None = None,
        mcp: McpHub | None = None,
        mcp_file: Path | None = None,
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
            personal: What is remembered about the user, handed to every agent. ``None``
                means nothing is kept.
            mcp: The connected apps, started with the model and handed to every agent.
            mcp_file: Where the apps are listed, so they can be added and removed while Tzara
                runs. ``None`` means they can only be changed by editing the file.
        """
        self._config = config
        self._agent_factory = agent_factory
        self._memory = memory or ConversationMemory()
        self._name_store = name_store
        self._personal = personal
        self._mcp = mcp
        self._mcp_file = mcp_file
        self._apps_reloading = False
        self._sign_in = SignIn()
        self._apps_problems: list[str] = []
        self._last_active = time.monotonic()
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
                if self._mcp is not None:
                    self._mcp.start()
                self._agent = self._agent_factory(
                    model=self._config.model,
                    config=self._config,
                    memory=self._memory,
                    personal=self._personal,
                    mcp=self._mcp,
                )
                self._agent.warm_up()
            except Exception as error:
                logger.exception("could not load the model")
                self._state = ServiceState.FAILED
                self._error = str(error)
            else:
                self._state = ServiceState.READY
                self._error = None

    def start_turn(
        self,
        text: str,
        sink: EventSink,
        context: ScreenContext | None = None,
        search_for: str | None = None,
    ) -> ChatTurn:
        """Begin answering ``text``.

        Args:
            text: The user's message.
            sink: Receives each event as it happens, then ``None``.
            context: What the user was looking at, if the app sent it.
            search_for: A question to search the web for, if the user pressed "Search online".

        Returns:
            The running turn, which can be cancelled.

        Raises:
            ServiceNotReady: If the model has not loaded.
            ServiceBusy: If another reply is being written.
        """
        if self._state is not ServiceState.READY or self._agent is None:
            raise ServiceNotReady(f"the model is not ready (state: {self._state.value})")
        if not self._lock.acquire(blocking=False):
            raise ServiceBusy("another reply is still being written")
        try:
            turn = ChatTurn(
                self._agent,
                text,
                sink,
                release=self._end_turn,
                context=context,
                search_for=search_for,
            )
            self._turn = turn
            turn.start()
        except BaseException:
            self._turn = None
            self._lock.release()
            raise
        return turn

    def write(self, request: WriteRequest) -> WriteResult:
        """Write text for a field of another app, if no reply is being written.

        Raises:
            ServiceNotReady: If the model has not loaded.
            ServiceBusy: If a reply is being written.
            LookupError: If writing into other apps is switched off.
            ValueError: If the model wrote nothing (the message says so, for the user).
        """
        if not self._config.write.enabled:
            raise LookupError("writing into other apps is off")
        if self._state is not ServiceState.READY or self._agent is None:
            raise ServiceNotReady(f"the model is not ready (state: {self._state.value})")
        if not self._lock.acquire(blocking=False):
            raise ServiceBusy("another reply is still being written")
        try:
            return self._agent.write(request)
        except ValueError as error:
            raise ValueError(self._config.prompts.write_empty) from error
        finally:
            self._last_active = time.monotonic()
            self._lock.release()

    def wake(self) -> bool:
        """Get the model ready again if it has been idle, without making anyone wait.

        Ollama unloads an idle model, and loading it again (and reading the prompt) takes about
        twenty seconds. A client calls this when the user opens the chat box, so that the work is
        done while they type. It does nothing while the server is loading or busy, or if it was
        in use recently, and it never blocks a reply.

        Returns:
            Whether the model is being woken.
        """
        agent = self._agent
        idle = time.monotonic() - self._last_active
        if (
            agent is None
            or self._state is not ServiceState.READY
            or self.busy
            or idle < self._config.server.wake_after_seconds
        ):
            return False
        self._last_active = time.monotonic()
        threading.Thread(target=self._warm, args=(agent,), name="tzara-wake", daemon=True).start()
        return True

    @staticmethod
    def _warm(agent: Tzara) -> None:
        """Load the model and read the prompt, ignoring failure: this was only a head start."""
        try:
            agent.warm_up()
        except Exception:
            logger.warning("could not wake the model", exc_info=True)

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
        if not self._lock.acquire(blocking=False):
            raise ServiceBusy("the agent is busy")
        try:
            self._memory.reset()
            logger.info("new conversation started")
        finally:
            self._lock.release()

    def resume_conversation(self, conversation_id: int) -> bool:
        """Carry on an earlier conversation. Returns whether there was one.

        Raises:
            ServiceBusy: If the agent is answering or loading.
        """
        if not self._lock.acquire(blocking=False):
            raise ServiceBusy("the agent is busy")
        try:
            if not self._memory.resume(conversation_id):
                return False
            if self._agent is not None:
                self._agent.resume_history()
            logger.info("conversation %d resumed", conversation_id)
            return True
        finally:
            self._lock.release()

    @property
    def conversation_id(self) -> int | None:
        """The current conversation's id in the log, if anything was said in it."""
        return self._memory.conversation_id

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

    def apps_overview(self) -> dict[str, Any]:
        """The apps, the ready-made ones to add, and whether a change is being applied."""
        return {
            "apps": self.apps(),
            "presets": [
                {
                    "id": preset.id,
                    "name": preset.server.name,
                    "title": preset.title,
                    "description": preset.description,
                    "first_use": preset.first_use,
                    "token_label": preset.token_label,
                    "token_help": preset.token_help,
                    "added": any(app["name"] == preset.server.name for app in self.apps()),
                }
                for preset in McpPresets.all()
            ],
            "reloading": self._apps_reloading,
            "problems": list(self._apps_problems),
            "editable": self._mcp_file is not None,
        }

    def add_app(self, server: McpServerConfig) -> None:
        """Add an app (or replace the one of the same name) and connect to it in the background.

        Only this app is connected: the others stay as they are.

        Raises:
            ValueError: If the app is not allowed, or apps cannot be changed here.
            ServiceBusy: If a reply is being written or another change is being applied.
        """
        servers = self._editable_servers()
        self._write_apps(McpConfigFile.with_server(servers, server))
        self._change_apps(lambda hub: hub.add(server))

    def remove_app(self, name: str) -> None:
        """Remove an app and disconnect from it in the background. The others stay as they are.

        Raises:
            ValueError: If apps cannot be changed here, or there is no such app.
            ServiceBusy: If a reply is being written or another change is being applied.
        """
        servers = self._editable_servers()
        if all(server.name != name for server in servers):
            raise ValueError("no such app")
        self._write_apps(McpConfigFile.without_server(servers, name))
        if self._auth_file is not None:
            TokenFile(self._auth_file).forget(name)
        self._change_apps(lambda hub: hub.drop(name))

    def _editable_servers(self) -> list[McpServerConfig]:
        """The apps in the file now, after checking that a change can be made."""
        if self._mcp_file is None or not self._config.mcp.enabled:
            raise ValueError("apps cannot be changed here")
        if self._turn is not None or self._apps_reloading or self._state is not ServiceState.READY:
            raise ServiceBusy("Tzara is busy; try again in a moment")
        return McpConfigFile.load(self._mcp_file)[0]

    def _write_apps(self, servers: list[McpServerConfig]) -> None:
        assert self._mcp_file is not None
        McpConfigFile.save(self._mcp_file, servers)
        self._apps_reloading = True

    def _change_apps(self, change: Callable[[McpHub], None]) -> None:
        """Apply ``change`` on a thread of its own: connecting takes a while."""
        threading.Thread(
            target=self._apply_quietly, args=(change,), name="tzara-apps", daemon=True
        ).start()

    def _apply_quietly(self, change: Callable[[McpHub], None]) -> None:
        """Apply the change, logging a failure: the apps that were there stay in use."""
        try:
            self.apply_apps(change)
        except Exception:
            logger.exception("could not apply the change to the connected apps")

    def _reconnect(self, name: str) -> None:
        """Connect to one app again (after signing in to it), leaving the others as they are."""
        assert self._mcp_file is not None
        server = next((s for s in McpConfigFile.load(self._mcp_file)[0] if s.name == name), None)
        if server is not None:
            self._apps_reloading = True
            self._apply_quietly(lambda hub: hub.add(server))

    @property
    def tool_limit(self) -> int:
        """How many characters of prompt an app's tools may take together."""
        return self._config.mcp.max_prompt_chars

    def app_tools(self, name: str) -> list[dict[str, Any]]:
        """Every tool of an app, and whether it is on, for the Settings screen.

        Raises:
            ValueError: If there is no such connected app.
        """
        server = self._app_config(name)
        assert self._mcp is not None
        return McpTools.listing(self._mcp, server, self._config.mcp, self._config.prompts)

    def set_app_tools(self, name: str, tools: list[str]) -> None:
        """Choose which tools of an app the model may use, and apply it.

        Raises:
            ValueError: If there is no such app or tool, or the tools together are too big for the
                prompt (the message says by how much, so the user can untick some).
            ServiceBusy: If a reply is being written or another change is being applied.
        """
        server = self._app_config(name)
        assert self._mcp is not None and self._mcp_file is not None
        known = {row["name"]: row["chars"] for row in self.app_tools(name)}
        if unknown := [tool for tool in tools if tool not in known]:
            raise ValueError(f"{name} has no tool called {unknown[0]!r}")
        limit = self._config.mcp.max_prompt_chars
        if (used := sum(known[tool] for tool in tools)) > limit:
            raise ValueError(
                f"That is too many tools: they use {used} characters of the {limit} that fit "
                "in a reply, and every extra character makes every reply slower. Untick some."
            )
        servers = self._editable_servers()
        updated = replace(server, tools=tuple(tools))
        self._write_apps(McpConfigFile.with_server(servers, updated))
        self._change_apps(lambda hub: hub.reconfigure(updated))

    def _app_config(self, name: str) -> McpServerConfig:
        if self._mcp is None or self._mcp_file is None:
            raise ValueError("apps cannot be changed here")
        server = next((s for s in McpConfigFile.load(self._mcp_file)[0] if s.name == name), None)
        if server is None or not any(status.name == name for status in self._mcp.status()):
            raise ValueError("no such app")
        return server

    def apply_apps(self, change: Callable[[McpHub], None]) -> None:
        """Change the running apps, then give their tools to a new agent.

        The conversation carries over. The agent is replaced even if the change failed, so what
        it offers always matches what is connected.

        Raises:
            ServiceBusy: If a reply is being written.
            ValueError: If apps cannot be changed here.
        """
        if self._mcp_file is None:
            raise ValueError("apps cannot be changed here")
        if not self._lock.acquire(blocking=False):
            self._apps_reloading = False
            raise ServiceBusy("the agent is busy")
        previous_state = self._state
        try:
            self._apps_reloading = True
            self._state = ServiceState.LOADING
            self._apps_problems = McpConfigFile.load(self._mcp_file)[1]
            hub = self._mcp
            if hub is None:
                hub = McpHub(self._config.mcp, [], self._auth_file)
                hub.start()
            change(hub)
            candidate = self._agent_factory(
                model=self.model,
                config=self._config,
                memory=self._memory,
                personal=self._personal,
                mcp=hub if hub.servers else None,
            )
            candidate.warm_up()
        except BaseException:
            self._state = previous_state
            raise
        else:
            self._mcp, self._agent = hub, candidate
            self._state = ServiceState.READY
        finally:
            self._apps_reloading = False
            self._lock.release()

    def apps(self) -> list[dict[str, Any]]:
        """The connected apps, and what became of each, for a client to show.

        While a change is being applied, an app that is in the file but not connected yet is
        listed too, so the user sees it is on its way.
        """
        if self._mcp is None:
            return []
        shown = [
            {
                "name": status.name,
                "connected": status.connected,
                "error": status.error,
                "offered": list(status.offered),
                "hidden": [{"tool": tool, "reason": reason} for tool, reason in status.hidden],
                "sign_in": self._can_sign_in(status.name),
            }
            for status in self._mcp.status()
        ]
        if self._apps_reloading and self._mcp_file is not None:
            known = {app["name"] for app in shown}
            for server in McpConfigFile.load(self._mcp_file)[0]:
                if server.name not in known:
                    shown.append(
                        {
                            "name": server.name,
                            "connected": False,
                            "error": None,
                            "offered": [],
                            "hidden": [],
                            "sign_in": self._can_sign_in(server.name),
                        }
                    )
        return shown

    def _can_sign_in(self, name: str) -> bool:
        """Whether the app has a Sign in button: a login of its own, or a remote address."""
        if self._signin_args(name) is not None:
            return True
        if self._mcp_file is None:
            return False
        return any(s.url for s in McpConfigFile.load(self._mcp_file)[0] if s.name == name)

    @property
    def _auth_file(self) -> Path | None:
        """Where the sign-in tokens of remote apps are kept, beside ``mcp.json``."""
        return None if self._mcp_file is None else self._mcp_file.with_name(self.AUTH_NAME)

    @staticmethod
    def _signin_args(name: str) -> tuple[str, ...] | None:
        """What makes the ready-made app called ``name`` log in, or ``None`` if it needs none."""
        for preset in McpPresets.all():
            if preset.server.name == name and preset.signin_args:
                return preset.signin_args
        return None

    def start_sign_in(self, name: str) -> SignInState:
        """Run the app's login, so the user gets a link and a code; reconnect when it worked.

        Raises:
            ValueError: If there is no such app, or it has no sign-in.
        """
        if self._mcp_file is None:
            raise ValueError("this app has no sign-in")
        server = next((s for s in McpConfigFile.load(self._mcp_file)[0] if s.name == name), None)
        if server is None:
            raise ValueError("no such app")
        if server.url:
            assert self._auth_file is not None
            return self._sign_in.start_remote(
                server,
                self._auth_file,
                self._config.mcp,
                self._open_in_browser,
                lambda: self._reconnect(name),
            )
        extra = self._signin_args(name)
        if extra is None:
            raise ValueError("this app has no sign-in")
        path = McpHub(self._config.mcp, []).search_path()
        program = shutil.which(server.command, path=path)
        if program is None:
            raise ValueError(f"{server.command!r} was not found. Install Node.js first.")
        return self._sign_in.start(server, program, path, extra, lambda: self._reconnect(name))

    @staticmethod
    def _open_in_browser(url: str) -> None:
        """Show the user a page. Only https sign-in pages the remote app itself named."""
        if url.startswith("https://"):
            webbrowser.open(url)

    def sign_in_state(self, name: str) -> SignInState | None:
        """How the latest sign-in to ``name`` is going."""
        return self._sign_in.state(name)

    def memory_overview(self) -> dict[str, Any]:
        """What is remembered about the user, for them to read.

        Returns:
            ``enabled``, the ``facts`` (id, text and when each was first remembered) and the
            number of stored ``conversations``.
        """
        if self._personal is None:
            return {"enabled": False, "facts": [], "conversations": 0}
        return {
            "enabled": True,
            "facts": [
                {"id": fact.id, "text": fact.text, "created": fact.created_at}
                for fact in self._personal.facts.all()
            ],
            "conversations": self._personal.log.count(),
            "activity": [
                {"at": item.at, "kind": item.kind, "summary": item.summary}
                for item in self._personal.activity.latest(200)
            ],
            "writing_samples": [
                {"place": place, "text": text, "at": at}
                for place, text, at in self._personal.styles.all()
            ],
            "chats": [
                {"id": c.id, "started": c.started_at, "title": c.title, "messages": c.messages}
                for c in self._personal.log.recent(60)
            ],
        }

    def memory_conversation(self, conversation_id: int) -> list[dict[str, Any]]:
        """Everything said in one stored conversation, for the dashboard to read."""
        if self._personal is None:
            return []
        return [
            {"role": m.role, "text": m.text, "at": m.at}
            for m in self._personal.log.transcript(conversation_id)
        ]

    def forget_fact(self, fact_id: int) -> bool:
        """Delete one remembered fact. Returns whether there was one."""
        return self._personal is not None and self._personal.facts.remove(fact_id)

    def erase_memory(self) -> None:
        """Forget everything: every fact and every stored conversation, and this conversation.

        Raises:
            ServiceBusy: If a reply is being written.
        """
        if not self._lock.acquire(blocking=False):
            raise ServiceBusy("the agent is busy")
        try:
            self._memory.reset()
            if self._personal is not None:
                self._personal.erase()
            logger.info("memory erased")
        finally:
            self._lock.release()

    def history(self) -> list[HistoryMessage]:
        """Return the conversation so far. Empty before the model has loaded."""
        return self._agent.history() if self._agent else []

    def _replace_agent(self, model: str, config: TzaraConfig) -> None:
        """Build, load and swap in a new agent, keeping the old one if that fails.

        Raises:
            ServiceBusy: If the agent is answering or loading.
            Exception: Whatever building or loading the new agent raises.
        """
        if not self._lock.acquire(blocking=False):
            raise ServiceBusy("the agent is busy")
        previous_state = self._state
        try:
            self._state = ServiceState.LOADING
            candidate = self._agent_factory(
                model=model,
                config=config,
                memory=self._memory,
                personal=self._personal,
                mcp=self._mcp,
            )
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
        if self._mcp is not None:
            self._mcp.close()

    def _end_turn(self) -> None:
        """Free the service once a turn is over."""
        self._last_active = time.monotonic()
        self._turn = None
        self._lock.release()
