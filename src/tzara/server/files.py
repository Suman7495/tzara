"""The files a running server leaves for its clients: token, address, process id."""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from tzara.config import ServerSettings
from tzara.paths import DataLocation


@dataclass(frozen=True)
class ServerPaths:
    """Locations of the server's runtime files.

    Attributes:
        directory: Directory holding all of them.
    """

    directory: Path

    TOKEN_NAME = "token"
    INFO_NAME = "server.json"
    NAME_NAME = "name"

    @classmethod
    def from_settings(cls, settings: ServerSettings) -> ServerPaths:
        """Use the configured data directory, or the platform default.

        Args:
            settings: Server settings.

        Returns:
            The paths to use.
        """
        return cls(settings.data_dir or DataLocation.default_dir())

    @property
    def token_file(self) -> Path:
        """File holding the API token."""
        return self.directory / self.TOKEN_NAME

    @property
    def info_file(self) -> Path:
        """File describing the running server."""
        return self.directory / self.INFO_NAME

    @property
    def name_file(self) -> Path:
        """File holding the assistant's name."""
        return self.directory / self.NAME_NAME


class TokenStore:
    """Keeps the secret that clients must present to use the server.

    The token is what stops other programs, and web pages in your browser, from
    driving the agent through the local port. It is stored in a file only the
    current user can read, which is how the overlay learns it.
    """

    PRIVATE_FILE_MODE = 0o600
    PRIVATE_DIR_MODE = 0o700

    def __init__(self, path: Path) -> None:
        """Create a store.

        Args:
            path: File to keep the token in.
        """
        self._path = path

    def load_or_create(self) -> str:
        """Return the token, creating the file on first use.

        An existing token is kept, so clients stay connected across restarts. A
        file that is empty is replaced, and one readable by other users has its
        permissions tightened.

        Returns:
            The token.
        """
        existing = self._read()
        if existing:
            self._restrict(self._path, self.PRIVATE_FILE_MODE)
            return existing

        token = secrets.token_urlsafe(32)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._restrict(self._path.parent, self.PRIVATE_DIR_MODE)
        # Create with owner-only permissions from the start: writing first and
        # restricting afterwards would leave a window where others can read it.
        descriptor = os.open(
            self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, self.PRIVATE_FILE_MODE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
        return token

    def _read(self) -> str:
        """Return the stored token, or an empty string if there is none."""
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    @staticmethod
    def _restrict(path: Path, mode: int) -> None:
        """Remove any permissions beyond ``mode`` from ``path``."""
        if stat.S_IMODE(path.stat().st_mode) != mode:
            path.chmod(mode)


@dataclass(frozen=True)
class ServerInfo:
    """Where a running server can be reached.

    Attributes:
        host: Address it listens on.
        port: TCP port.
        pid: Process id of the server.
    """

    host: str
    port: int
    pid: int

    @property
    def url(self) -> str:
        """Base URL for requests to the server."""
        return f"http://{self.host}:{self.port}"


class ServerInfoFile:
    """Reads and writes the file that tells clients where the server is."""

    def __init__(self, path: Path) -> None:
        """Create the accessor.

        Args:
            path: The ``server.json`` file.
        """
        self._path = path

    def write(self, info: ServerInfo) -> None:
        """Record a running server, replacing any earlier record."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(info)), encoding="utf-8")

    def read(self) -> ServerInfo | None:
        """Return the recorded server, or ``None`` if there is no usable record."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return ServerInfo(host=str(data["host"]), port=int(data["port"]), pid=int(data["pid"]))
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return None

    def remove_if_owned_by(self, pid: int) -> None:
        """Delete the record, but only if it describes process ``pid``.

        A server that fails to start must not erase the record of the one that
        is already running.
        """
        info = self.read()
        if info is not None and info.pid == pid:
            self._path.unlink(missing_ok=True)


class NameStore:
    """Remembers what the assistant has been named, across server restarts."""

    def __init__(self, path: Path) -> None:
        """Create a store.

        Args:
            path: File to keep the name in.
        """
        self._path = path

    def load(self) -> str | None:
        """Return the saved name, or ``None`` if there is none or it is unusable."""
        try:
            lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        except (FileNotFoundError, UnicodeDecodeError):
            return None
        return lines[0].strip() if len(lines) == 1 and lines[0].strip() else None

    def save(self, name: str) -> None:
        """Remember ``name``, replacing any earlier one."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(name, encoding="utf-8")
