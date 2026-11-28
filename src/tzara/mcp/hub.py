"""Runs the connections to MCP servers, and calls their tools for the (synchronous) agent."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import Tool

from tzara.config import McpSettings
from tzara.mcp.config import McpServerConfig
from tzara.mcp.oauth import RemoteSignInProvider, TokenFile, explain

logger = logging.getLogger(__name__)


class _NotStartable(Exception):
    """The app cannot even be started here; the message says why, for the user."""


@dataclass
class ServerStatus:
    """What became of one app.

    Attributes:
        name: The app.
        connected: Whether it started and listed its tools.
        error: Why it did not, if it did not.
        offered: The tools given to the model.
        hidden: Tools not given, each with the reason (usually: not marked read-only).
    """

    name: str
    connected: bool = False
    error: str | None = None
    offered: list[str] = field(default_factory=list)
    hidden: list[tuple[str, str]] = field(default_factory=list)


class McpHub:
    """Starts the apps in ``mcp.json``, keeps them running, and calls their tools.

    The agent is synchronous and the protocol is not, so the connections live on a thread of
    their own with its own event loop; :meth:`call` crosses over to it. A server that fails to
    start, or stops answering, costs only its own tools: it is reported and the rest carry on.
    """

    def __init__(
        self,
        settings: McpSettings,
        servers: list[McpServerConfig],
        auth_file: Path | None = None,
    ) -> None:
        """Create the hub. Nothing is started until :meth:`start`.

        Args:
            settings: Limits and timeouts.
            servers: The apps to connect to.
            auth_file: Where the sign-in tokens of remote apps are kept. ``None``: remote apps
                cannot sign in.
        """
        self._tokens = TokenFile(auth_file) if auth_file else None
        self._auth = (
            RemoteSignInProvider(self._tokens, settings.callback_port) if self._tokens else None
        )
        self._settings = settings
        self._servers = [server for server in servers if server.enabled]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closing: asyncio.Event | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._status: dict[str, ServerStatus] = {}
        self._remote: dict[str, list[Tool]] = {}
        self._stops: dict[str, asyncio.Event] = {}
        self._ready: dict[str, threading.Event] = {}

    @property
    def servers(self) -> list[McpServerConfig]:
        """The apps this hub was asked to connect to (the enabled ones)."""
        return list(self._servers)

    @property
    def settings(self) -> McpSettings:
        """The limits and timeouts."""
        return self._settings

    def status(self) -> list[ServerStatus]:
        """What became of each app."""
        return [
            self._status[server.name] for server in self._servers if server.name in self._status
        ]

    def start(self) -> None:
        """Start every app and wait, at most ``startup_seconds``, for them to list their tools.

        Never raises: a failure is a status.
        """
        if self._thread is None:
            self._ensure_thread()
            for server in self._servers:
                self._begin(server)
            self._wait_for(self._servers)

    def add(self, server: McpServerConfig) -> None:
        """Connect one more app (or reconnect one) without touching the others.

        Waits, at most ``startup_seconds``, for it to list its tools. Never raises.
        """
        self._ensure_thread()
        self.drop(server.name)
        self._servers.append(server)
        self._begin(server)
        self._wait_for([server])

    def reconfigure(self, server: McpServerConfig) -> None:
        """Change an app's settings that do not need a new connection (which tools may be used)."""
        self._servers = [server if known.name == server.name else known for known in self._servers]

    def drop(self, name: str) -> None:
        """Disconnect one app and forget it, leaving the others as they are."""
        stop = self._stops.get(name)
        if stop is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(stop.set)
            for _ in range(50):  # let its program shut down: at most five seconds
                if name not in self._stops:
                    break
                time.sleep(0.1)
        self._servers = [server for server in self._servers if server.name != name]
        self._status.pop(name, None)
        self._remote.pop(name, None)

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        started = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(started,), name="tzara-mcp", daemon=True
        )
        self._thread.start()
        started.wait(timeout=5)

    def _begin(self, server: McpServerConfig) -> None:
        """Start connecting to one app on the connections' thread, without waiting."""
        assert self._loop is not None
        self._status[server.name] = ServerStatus(server.name)
        ready = self._ready[server.name] = threading.Event()

        async def go() -> None:
            stop = self._stops[server.name] = asyncio.Event()
            try:
                await self._serve(server, stop, ready)
            finally:
                self._stops.pop(server.name, None)

        asyncio.run_coroutine_threadsafe(go(), self._loop)

    def _wait_for(self, servers: list[McpServerConfig]) -> None:
        """Wait until each app is up or has failed, or the time for starting is out."""
        deadline = time.monotonic() + self._settings.startup_seconds
        for server in servers:
            ready = self._ready.get(server.name)
            if ready is not None and not ready.wait(max(0.0, deadline - time.monotonic())):
                self._status[server.name].error = "did not start in time"

    def tools(self, server: str) -> list[Tool]:
        """The tools ``server`` lists, all of them."""
        return list(self._remote.get(server, []))

    def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        """Call a tool and return the protocol's result.

        Raises:
            RuntimeError: If the app is not connected.
            TimeoutError: If it does not answer in ``call_seconds``.
        """
        if self._loop is None or server not in self._sessions:
            raise RuntimeError(f"{server} is not connected")
        future = asyncio.run_coroutine_threadsafe(self._call(server, tool, arguments), self._loop)
        try:
            return future.result(timeout=self._settings.call_seconds + 5)
        except TimeoutError as error:
            future.cancel()
            raise TimeoutError(f"{server} did not answer in time") from error

    def close(self) -> None:
        """Stop every app and the thread. Safe to call more than once."""
        loop, closing = self._loop, self._closing
        if loop is not None and closing is not None and loop.is_running():
            loop.call_soon_threadsafe(closing.set)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    # -- on the connections' own thread -------------------------------------------------------

    def _run(self, started: threading.Event) -> None:
        """The thread: an event loop that lives until :meth:`close`."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._closing = asyncio.Event()
        started.set()
        try:
            loop.run_until_complete(self._closing.wait())
            for stop in list(self._stops.values()):
                stop.set()
            loop.run_until_complete(asyncio.sleep(0.2))  # let the servers shut down
        finally:
            loop.close()

    async def _serve(
        self, server: McpServerConfig, stop: asyncio.Event, ready: threading.Event
    ) -> None:
        """Keep one app connected until the hub closes, whatever kind of app it is."""
        status = self._status[server.name]
        try:
            async with self._open(server) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                self._remote[server.name] = (await session.list_tools()).tools
                self._sessions[server.name] = session
                status.connected = True
                ready.set()
                await stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # the SDK's task groups raise groups
            status.error = status.error or self._why(server, error)
            logger.warning("could not connect to %s: %s (%s)", server.name, status.error, error)
        finally:
            self._sessions.pop(server.name, None)
            status.connected = False
            ready.set()

    def _open(self, server: McpServerConfig) -> AbstractAsyncContextManager[Any]:
        """The connection to an app: a program started here, or a remote address.

        Raises:
            _NotStartable: If the program is not installed, or remote apps cannot be used.
        """
        if server.url:
            if self._auth is None:
                raise _NotStartable("remote apps need the server's data folder")
            return self._remote_connection(server, self._auth)
        path = self.search_path()
        program = shutil.which(server.command, path=path)
        if program is None:
            raise _NotStartable(
                f"{server.command!r} was not found. Install it (npx comes with Node.js) "
                "or give the full path in the command."
            )
        parameters = StdioServerParameters(
            command=program, args=list(server.args), env={"PATH": path, **server.env}
        )
        return stdio_client(parameters)

    @asynccontextmanager
    async def _remote_connection(
        self, server: McpServerConfig, auth: RemoteSignInProvider
    ) -> AsyncIterator[Any]:
        client = create_mcp_http_client(auth=auth.for_connecting(server.name, server.url))
        async with client, streamable_http_client(server.url, http_client=client) as streams:
            yield streams

    def _why(self, server: McpServerConfig, error: BaseException) -> str:
        """What to tell the user. A remote app never signed in to says so, whatever the SDK says."""
        if isinstance(error, _NotStartable):
            return str(error)
        if server.url and not (self._tokens and self._tokens.has_tokens(server.name)):
            return "sign in needed"
        return explain(error) if server.url else (str(error) or error.__class__.__name__)

    def search_path(self) -> str:
        """The folders programs are looked for in: the usual ones, then the extra ones for apps.

        Tzara's server runs under launchd, whose path holds only the system folders, so a
        program installed by Homebrew (``npx``, ``node``, ``uvx``) would not be found.
        """
        extra = [os.path.expanduser(folder) for folder in self._settings.extra_paths]
        usual = [folder for folder in os.environ.get("PATH", "").split(os.pathsep) if folder]
        ordered = [folder for folder in [*usual, *extra] if os.path.isdir(folder)]
        return os.pathsep.join(dict.fromkeys(ordered))

    async def _call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        """One tool call, with its time limit."""
        return await asyncio.wait_for(
            self._sessions[server].call_tool(tool, arguments), self._settings.call_seconds
        )
