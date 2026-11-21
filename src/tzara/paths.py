"""Where Tzara keeps its files."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


class DataLocation:
    """Finds the platform's standard directory for Tzara's runtime files.

    These are the API token, the file describing the running server, and its
    process id. Nothing here is a log (see :class:`tzara.logs.LogLocation`).
    """

    APP_NAME = "tzara"

    @classmethod
    def default_dir(
        cls,
        platform: str | None = None,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> Path:
        """Return the default data directory for a platform.

        Args:
            platform: A ``sys.platform`` value. Defaults to the running platform.
            env: Environment variables. Defaults to ``os.environ``.
            home: Home directory. Defaults to the user's home.

        Returns:
            ``~/Library/Application Support/tzara`` on macOS, ``%APPDATA%/tzara``
            on Windows, and ``$XDG_DATA_HOME/tzara`` (``~/.local/share/tzara``)
            elsewhere.
        """
        platform = sys.platform if platform is None else platform
        env = os.environ if env is None else env
        home = Path.home() if home is None else home

        if platform == "darwin":
            base = home / "Library" / "Application Support"
        elif platform == "win32":
            base = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        else:
            base = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
        return base / cls.APP_NAME
