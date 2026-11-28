"""Signing in to a remote app (OAuth in the browser) and keeping the tokens."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

logger = logging.getLogger(__name__)


class NeedsSignIn(Exception):
    """The remote app wants the user to sign in first."""


class TokenFile:
    """The tokens and the registered client of each remote app, in one file only the user reads."""

    MODE = 0o600

    def __init__(self, path: Path) -> None:
        """Create the store.

        Args:
            path: The file.
        """
        self._path = path
        self._lock = threading.Lock()

    def for_app(self, name: str) -> AppTokens:
        """The storage the OAuth code uses for one app."""
        return AppTokens(self, name)

    def has_tokens(self, name: str) -> bool:
        """Whether the user has signed in to ``name``."""
        return bool(self._read().get(name, {}).get("tokens"))

    def forget(self, name: str) -> None:
        """Delete what is kept for ``name`` (it is removed from Tzara)."""
        with self._lock:
            data = self._read()
            if data.pop(name, None) is not None:
                self._write(data)

    def get(self, name: str, part: str) -> dict[str, Any] | None:
        """One saved part (``tokens`` or ``client``) of an app."""
        value = self._read().get(name, {}).get(part)
        return value if isinstance(value, dict) else None

    def put(self, name: str, part: str, value: dict[str, Any]) -> None:
        """Save one part of an app."""
        with self._lock:
            data = self._read()
            data.setdefault(name, {})[part] = value
            self._write(data)

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, self.MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        temporary.replace(self._path)


class AppTokens:
    """What the SDK's OAuth code asks of a store, kept per app in a :class:`TokenFile`."""

    def __init__(self, file: TokenFile, name: str) -> None:
        """Create the storage of one app."""
        self._file = file
        self._name = name

    async def get_tokens(self) -> OAuthToken | None:
        """The saved tokens, if the user has signed in."""
        saved = self._file.get(self._name, "tokens")
        return OAuthToken.model_validate(saved) if saved else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Save tokens (also called when they are refreshed)."""
        self._file.put(self._name, "tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """The registration of Tzara with this app, if it has been made."""
        saved = self._file.get(self._name, "client")
        return OAuthClientInformationFull.model_validate(saved) if saved else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Save the registration."""
        self._file.put(self._name, "client", client_info.model_dump(mode="json", exclude_none=True))


class CallbackServer:
    """Catches the browser's return from the sign-in page, on this computer only."""

    PAGE = (
        b"<html><body style='font-family:system-ui;margin:4em'><h2>Signed in</h2>"
        b"<p>You can close this tab and go back to Tzara.</p></body></html>"
    )

    def __init__(self, port: int) -> None:
        """Create the server (it is not listening yet).

        Args:
            port: The port on 127.0.0.1. It is fixed, because the app remembers it as part of
                Tzara's registration.
        """
        self.port = port
        self._result: AuthorizationCodeResult | None = None
        self._arrived = threading.Event()
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        """Start listening.

        Raises:
            OSError: If the port is in use.
        """
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = parse_qs(urlparse(self.path).query)
                code = (query.get("code") or [""])[0]
                if urlparse(self.path).path != "/callback" or not code:
                    self.send_response(404)
                    self.end_headers()
                    return
                owner._result = AuthorizationCodeResult(
                    code=code,
                    state=query["state"][0] if query.get("state") else None,
                    iss=query["iss"][0] if query.get("iss") else None,
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(owner.PAGE)
                owner._arrived.set()

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True, name="tzara-oauth").start()

    async def wait(self, seconds: float) -> AuthorizationCodeResult:
        """Wait for the browser to come back.

        Raises:
            TimeoutError: If the user did not finish in time.
        """
        arrived = await asyncio.to_thread(self._arrived.wait, seconds)
        if not arrived or self._result is None:
            raise TimeoutError("the sign-in was not finished in time")
        return self._result

    def stop(self) -> None:
        """Stop listening."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class RemoteSignInProvider:
    """Builds the OAuth handler for one remote app, for connecting or for signing in."""

    def __init__(self, file: TokenFile, port: int) -> None:
        """Create the builder.

        Args:
            file: Where tokens are kept.
            port: The fixed port of the return address.
        """
        self._file = file
        self._port = port

    def for_connecting(self, name: str, url: str) -> OAuthClientProvider:
        """The handler used when the app starts: it never opens a browser by itself.

        Without tokens it raises :class:`NeedsSignIn`, which the status shows as "sign in".
        """

        async def refuse(_url: str) -> None:
            raise NeedsSignIn("sign in needed")

        return self._provider(name, url, refuse, self._nothing)

    def for_signing_in(
        self,
        name: str,
        url: str,
        server: CallbackServer,
        opened: Callable[[str], Awaitable[None]],
        seconds: float,
    ) -> OAuthClientProvider:
        """The handler used when the user pressed Sign in: ``opened`` gets the page to show."""

        async def callback() -> AuthorizationCodeResult:
            return await server.wait(seconds)

        return self._provider(name, url, opened, callback)

    @staticmethod
    async def _nothing() -> AuthorizationCodeResult:
        raise NeedsSignIn("sign in needed")

    def _provider(
        self,
        name: str,
        url: str,
        redirect: Callable[[str], Awaitable[None]],
        callback: Callable[[], Awaitable[AuthorizationCodeResult]],
    ) -> OAuthClientProvider:
        metadata = OAuthClientMetadata(
            client_name="Tzara",
            redirect_uris=[f"http://127.0.0.1:{self._port}/callback"],  # type: ignore[list-item]
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )
        return OAuthClientProvider(
            server_url=url,
            client_metadata=metadata,
            storage=self._file.for_app(name),
            redirect_handler=redirect,
            callback_handler=callback,
        )


def explain(error: BaseException) -> str:
    """What went wrong, in a sentence, seeing through the groups the SDK's task groups raise."""
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, NeedsSignIn):
            return "sign in needed"
        pending.extend(getattr(current, "exceptions", ()))
    return str(error) or error.__class__.__name__
