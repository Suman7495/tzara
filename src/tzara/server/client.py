"""A small client for asking a running server how it is doing."""

from __future__ import annotations

from typing import Any

import httpx

from tzara.server.files import ServerInfo


class ServerClient:
    """Checks on a running Tzara server.

    This is not the chat client: it only answers "is it up, and in what state",
    for ``tzara start`` and ``tzara status``.
    """

    def __init__(self, info: ServerInfo, token: str | None = None, timeout: float = 2.0) -> None:
        """Create a client.

        Args:
            info: Where the server is.
            token: The API token. Without it only :meth:`is_healthy` works.
            timeout: Seconds to wait for each request.
        """
        self._info = info
        self._token = token
        self._timeout = timeout

    def is_healthy(self) -> bool:
        """Say whether a Tzara server answers at the recorded address."""
        try:
            response = httpx.get(f"{self._info.url}/v1/health", timeout=self._timeout)
            return response.status_code == httpx.codes.OK and response.json() == {"status": "ok"}
        except (httpx.HTTPError, ValueError):
            return False

    def status(self) -> dict[str, Any] | None:
        """Return the server's state, or ``None`` if it cannot be read.

        That includes having no token, a token the server rejects, and no answer.
        """
        if not self._token:
            return None
        try:
            response = httpx.get(
                f"{self._info.url}/v1/status",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
            if response.status_code != httpx.codes.OK:
                return None
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return data if isinstance(data, dict) else None
