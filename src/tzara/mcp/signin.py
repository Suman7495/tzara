"""Signing in to an app: running its own login and showing the user the link and code."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from tzara.config import McpSettings
from tzara.mcp.config import McpServerConfig
from tzara.mcp.oauth import CallbackServer, RemoteSignInProvider, TokenFile, explain

logger = logging.getLogger(__name__)


@dataclass
class SignInState:
    """How a sign-in is going.

    Attributes:
        running: Whether the login program is still running.
        url: The page the user opens, once the program has said it.
        code: The code to enter there, once it has said it.
        message: What the program said, shortened, in case the link and code were not found.
        done: Whether it ended; ``error`` says why if it did not work.
        error: What went wrong.
    """

    running: bool = True
    url: str = ""
    code: str = ""
    message: str = ""
    done: bool = False
    error: str = ""


class SignIn:
    """Runs an app's login (the app's own program with ``--login``) and reports its progress.

    The login prints a device-code message: open a page, type a code. The user does that in
    their own browser, so Tzara never sees a password. The program keeps its token itself, and
    the app picks it up the next time it starts, so the caller reconnects when this ends.
    """

    URL = re.compile(r"https?://[^\s\"'<>]+")
    CODE = re.compile(r"\bcode\s+([A-Z0-9]{6,12})\b")
    MAX_MESSAGE_CHARS = 300

    def __init__(self, seconds: float = 600.0) -> None:
        """Create the helper.

        Args:
            seconds: How long the user has to finish signing in before it is given up.
        """
        self._seconds = seconds
        self._states: dict[str, SignInState] = {}
        self._lock = threading.Lock()

    def state(self, name: str) -> SignInState | None:
        """How the latest sign-in to ``name`` is going, or ``None`` if none was started."""
        with self._lock:
            return self._states.get(name)

    def start(
        self,
        server: McpServerConfig,
        program: str,
        search_path: str,
        extra_args: tuple[str, ...],
        on_success: Callable[[], None],
    ) -> SignInState:
        """Start signing in, unless one is already running for this app.

        Args:
            server: The app.
            program: The full path of its program.
            search_path: The folders its program may look in.
            extra_args: What to add to its arguments to make it log in.
            on_success: Called, on another thread, when the login worked.
        """
        with self._lock:
            current = self._states.get(server.name)
            if current is not None and current.running:
                return current
            state = SignInState()
            self._states[server.name] = state
        threading.Thread(
            target=self._run,
            args=(state, server, program, search_path, extra_args, on_success),
            name="tzara-signin",
            daemon=True,
        ).start()
        return state

    def _run(
        self,
        state: SignInState,
        server: McpServerConfig,
        program: str,
        search_path: str,
        extra_args: tuple[str, ...],
        on_success: Callable[[], None],
    ) -> None:
        environment = {
            key: value
            for key in ("HOME", "USER", "LOGNAME", "TMPDIR", "SHELL", "LANG")
            if (value := os.environ.get(key))
        }
        environment |= {"PATH": search_path, **server.env}
        try:
            process = subprocess.Popen(
                [program, *server.args, *extra_args],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as error:
            self._finish(state, f"could not start it: {error}")
            return
        timer = threading.Timer(self._seconds, process.kill)
        timer.start()
        try:
            assert process.stdout is not None
            for line in process.stdout:
                self._read(state, line)
            code = process.wait()
        finally:
            timer.cancel()
        if code == 0:
            self._finish(state, "")
            try:
                on_success()
            except Exception:
                logger.exception("could not reconnect %s after signing in", server.name)
        else:
            self._finish(state, state.message or f"the sign-in ended with status {code}")

    def _read(self, state: SignInState, line: str) -> None:
        text = line.strip()
        if not text:
            return
        url = self.URL.search(text)
        if url and not state.url:
            state.url = url.group(0).rstrip(".,)")
        code = self.CODE.search(text)
        if code and not state.code:
            state.code = code.group(1)
        if url or code or not (state.url or state.code):
            state.message = text[: self.MAX_MESSAGE_CHARS]

    def start_remote(
        self,
        server: McpServerConfig,
        auth_file: Path,
        settings: McpSettings,
        open_page: Callable[[str], None],
        on_success: Callable[[], None],
    ) -> SignInState:
        """Sign in to a remote app: open its sign-in page in the browser and wait for the user.

        Args:
            server: The remote app.
            auth_file: Where its tokens are kept.
            settings: Gives the return port and how long the user has.
            open_page: Shows the sign-in page to the user (opens the browser).
            on_success: Called, on another thread, when it worked.
        """
        with self._lock:
            current = self._states.get(server.name)
            if current is not None and current.running:
                return current
            state = SignInState()
            self._states[server.name] = state
        threading.Thread(
            target=self._run_remote,
            args=(state, server, auth_file, settings, open_page, on_success),
            name="tzara-signin",
            daemon=True,
        ).start()
        return state

    def _run_remote(
        self,
        state: SignInState,
        server: McpServerConfig,
        auth_file: Path,
        settings: McpSettings,
        open_page: Callable[[str], None],
        on_success: Callable[[], None],
    ) -> None:
        callback = CallbackServer(settings.callback_port)
        try:
            callback.start()
        except OSError as error:
            self._finish(state, f"port {settings.callback_port} is in use: {error}")
            return

        async def show(url: str) -> None:
            state.url = url
            state.message = "Finish signing in in your browser."
            open_page(url)

        async def connect() -> None:
            provider = RemoteSignInProvider(TokenFile(auth_file), settings.callback_port)
            auth = provider.for_signing_in(
                server.name, server.url, callback, show, settings.sign_in_seconds
            )
            client = create_mcp_http_client(auth=auth)
            async with (
                client,
                streamable_http_client(server.url, http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()

        try:
            asyncio.run(connect())
        except BaseException as error:  # the SDK raises groups
            self._finish(state, explain(error))
            return
        finally:
            callback.stop()
        self._finish(state, "")
        try:
            on_success()
        except Exception:
            logger.exception("could not reconnect %s after signing in", server.name)

    @staticmethod
    def _finish(state: SignInState, error: str) -> None:
        state.error = error
        state.running = False
        state.done = not error
