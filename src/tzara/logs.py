"""Logging setup: a rotating file, never the chat."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

from tzara.config import LogSettings


class LogLocation:
    """Finds the platform's standard place for a log file."""

    APP_NAME = "tzara"
    FILE_NAME = "tzara.log"

    @classmethod
    def default_file(
        cls,
        platform: str | None = None,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> Path:
        """Return the default log file path for a platform.

        Args:
            platform: A ``sys.platform`` value. Defaults to the running platform.
            env: Environment variables. Defaults to ``os.environ``.
            home: Home directory. Defaults to the user's home.

        Returns:
            ``~/Library/Logs/tzara`` on macOS, ``%LOCALAPPDATA%/tzara/Logs`` on
            Windows, and ``$XDG_STATE_HOME/tzara`` (``~/.local/state/tzara``)
            elsewhere.
        """
        platform = sys.platform if platform is None else platform
        env = os.environ if env is None else env
        home = Path.home() if home is None else home

        if platform == "darwin":
            directory = home / "Library" / "Logs" / cls.APP_NAME
        elif platform == "win32":
            directory = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
            directory = directory / cls.APP_NAME / "Logs"
        else:
            directory = Path(env.get("XDG_STATE_HOME") or home / ".local" / "state")
            directory = directory / cls.APP_NAME
        return directory / cls.FILE_NAME


class LogConfigurator:
    """Sends Tzara's logs, and warnings from libraries, to a rotating file.

    Nothing is written to the terminal: the console is the chat, and a stray
    log line would land in the middle of a reply.

    Tzara's own loggers use the configured level. Third-party loggers are held
    at ``WARNING`` so HTTP-client chatter does not fill the file.
    """

    FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    ROOT_LEVEL = logging.WARNING
    LOGGER_NAME = "tzara"

    def __init__(self, settings: LogSettings) -> None:
        """Create a configurator. Nothing changes until :meth:`install`.

        Args:
            settings: Level, file and rotation settings.
        """
        self._settings = settings
        self._handler: logging.Handler | None = None

    def install(self) -> Path | None:
        """Start logging to the file, replacing any earlier installation.

        If the file cannot be opened, a note goes to stderr and logging is
        silenced rather than leaking into the chat.

        Returns:
            The log file, or ``None`` if it could not be opened.
        """
        self.uninstall()
        path = self._settings.file or LogLocation.default_file()
        root = logging.getLogger()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = RotatingFileHandler(
                path,
                maxBytes=self._settings.max_bytes,
                backupCount=self._settings.backup_count,
                encoding="utf-8",
            )
        except OSError as error:
            print(f"tzara: cannot write log file {path}: {error}", file=sys.stderr)
            handler = logging.NullHandler()
            path_or_none = None
        else:
            path_or_none = path
            handler.setFormatter(logging.Formatter(self.FORMAT))

        root.addHandler(handler)
        root.setLevel(self.ROOT_LEVEL)
        logging.getLogger(self.LOGGER_NAME).setLevel(self._settings.level)
        logging.captureWarnings(True)
        self._handler = handler
        return path_or_none

    def uninstall(self) -> None:
        """Remove the handler added by :meth:`install`, if any."""
        if self._handler is None:
            return
        logging.getLogger().removeHandler(self._handler)
        self._handler.close()
        self._handler = None
        logging.captureWarnings(False)
