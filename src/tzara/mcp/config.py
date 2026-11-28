"""The list of apps to connect to: ``mcp.json``."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpServerConfig:
    """One app to connect to.

    Attributes:
        name: What the app is called; the start of each of its tool names.
        command: The program that starts the server (empty for a remote app, which has a ``url``).
        args: Its arguments.
        env: Extra environment variables for it, such as a token.
        tools: If given, only these tools are offered, whether or not they change anything.
        allow_actions: Offer tools that are not marked read-only. Off by default: nothing asks
            the user before a tool sends, deletes or changes something, so those are opt-in.
        enabled: Whether to connect at all.
        url: The address of a remote app (``https://...``) instead of a program. It signs the
            user in through the browser (OAuth) once, from Settings.
    """

    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    tools: tuple[str, ...] | None = None
    allow_actions: bool = False
    enabled: bool = True
    url: str = ""


class McpConfigFile:
    """Reads ``mcp.json``.

    The file looks like this::

        {"servers": {"files": {"command": "npx", "args": ["-y", "some-server"],
                               "env": {"TOKEN": "..."}, "tools": ["read_file"],
                               "allow_actions": false, "enabled": true}}}

    A problem in the file is reported and skipped, never fatal: Tzara starts without the app.
    """

    NAME: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9-]{1,30}$")

    @classmethod
    def load(cls, path: Path) -> tuple[list[McpServerConfig], list[str]]:
        """The apps listed in ``path``, and what was wrong with the file or its entries.

        A missing file is not a problem: it means no apps.
        """
        if not path.is_file():
            return [], []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return [], [f"{path.name} could not be read: {error}"]
        entries = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            return [], [f'{path.name} needs a "servers" object']
        servers: list[McpServerConfig] = []
        problems: list[str] = []
        for name, entry in entries.items():
            try:
                servers.append(cls._server(name, entry))
            except ValueError as error:
                problems.append(f"{name}: {error}")
        for problem in problems:
            logger.warning("mcp.json: %s", problem)
        return servers, problems

    @classmethod
    def _server(cls, name: str, entry: Any) -> McpServerConfig:
        """One entry, checked.

        Raises:
            ValueError: If the name or a field is not allowed.
        """
        if not cls.NAME.match(name):
            raise ValueError("the name may only use letters, digits and - (30 at most)")
        if not isinstance(entry, dict):
            raise ValueError('needs a "command" or a "url"')
        url = entry.get("url", "")
        if url:
            cls._check_url(url)
        elif not isinstance(entry.get("command"), str) or not entry["command"].strip():
            raise ValueError('needs a "command" or a "url"')
        args, env, tools = entry.get("args", []), entry.get("env", {}), entry.get("tools")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError('"args" must be a list of strings')
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env.items()
        ):
            raise ValueError('"env" must map names to strings')
        if tools is not None and (
            not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools)
        ):
            raise ValueError('"tools" must be a list of names')
        return McpServerConfig(
            name=name,
            command=entry.get("command", "") if not url else "",
            url=url,
            args=tuple(args),
            env=dict(env),
            tools=None if tools is None else tuple(tools),
            allow_actions=entry.get("allow_actions") is True,
            enabled=entry.get("enabled") is not False,
        )

    @staticmethod
    def _check_url(url: Any) -> None:
        """A remote app's address must be https (or plain http to this computer).

        Raises:
            ValueError: If it is not.
        """
        host = urlparse(url).hostname if isinstance(url, str) else None
        secure = isinstance(url, str) and url.startswith("https://") and host
        local = (
            isinstance(url, str)
            and url.startswith("http://")
            and host in ("127.0.0.1", "localhost")
        )
        if not (secure or local):
            raise ValueError('"url" must be an https address')

    @classmethod
    def save(cls, path: Path, servers: list[McpServerConfig]) -> None:
        """Write the apps to ``path``, readable by the user only (an app's token may be in it).

        The file is replaced in one step, so a crash cannot leave half of it.

        Raises:
            OSError: If it cannot be written.
        """
        entries: dict[str, Any] = {}
        for server in servers:
            entry: dict[str, Any] = (
                {"url": server.url} if server.url else {"command": server.command}
            )
            if server.args:
                entry["args"] = list(server.args)
            if server.env:
                entry["env"] = dict(server.env)
            if server.tools is not None:
                entry["tools"] = list(server.tools)
            if server.allow_actions:
                entry["allow_actions"] = True
            if not server.enabled:
                entry["enabled"] = False
            entries[server.name] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mcp-", suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"servers": entries}, handle, indent=2)
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise

    @classmethod
    def with_server(
        cls, servers: list[McpServerConfig], new: McpServerConfig
    ) -> list[McpServerConfig]:
        """``servers`` with ``new`` added, or replacing the app of the same name.

        Raises:
            ValueError: If the name or a field of ``new`` is not allowed.
        """
        cls._server(
            new.name,
            {
                "command": new.command,
                "url": new.url,
                "args": list(new.args),
                "env": new.env,
                "tools": None if new.tools is None else list(new.tools),
            },
        )
        return [server for server in servers if server.name != new.name] + [new]

    @staticmethod
    def without_server(servers: list[McpServerConfig], name: str) -> list[McpServerConfig]:
        """``servers`` without the app called ``name``."""
        return [server for server in servers if server.name != name]
