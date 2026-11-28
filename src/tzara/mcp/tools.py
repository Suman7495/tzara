"""An app's tool, as a tool the agent can call."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, ClassVar

from langchain_core.tools import BaseTool
from mcp.types import Tool

from tzara.config import McpSettings
from tzara.mcp.config import McpServerConfig
from tzara.mcp.hub import McpHub, ServerStatus
from tzara.prompts import Prompts

logger = logging.getLogger(__name__)


class McpTool(BaseTool):
    """One tool of one connected app.

    Its name is ``app__tool``, so the model (and the user's activity line) can tell where it
    comes from. Failures are returned as text for the model to read, as with every tool.
    """

    SEPARATOR: ClassVar[str] = "__"

    name: str = "app__tool"
    description: str = "A tool of a connected app."
    remote_name: str = ""
    server: str = ""

    def __init__(
        self,
        hub: McpHub,
        server: str,
        remote: Tool,
        prompts: Prompts,
        name: str,
        description: str,
        schema: dict[str, Any] | None = None,
    ) -> None:
        """Create the tool.

        Args:
            hub: Makes the call.
            server: The app.
            remote: The tool as the app describes it (its schema says what arguments it takes).
            prompts: Where the wording around a result comes from.
            name: The name given to the model.
            description: What the model is told, already shortened.
            schema: The argument list the model is told, already compacted.
        """
        arguments = schema or remote.input_schema or {"type": "object", "properties": {}}
        super().__init__(name=name, description=description, args_schema=arguments)
        self._arguments = arguments
        self._hub = hub
        self.server = server
        self.remote_name = remote.name
        self._prompts = prompts

    @property
    def arguments(self) -> dict[str, Any]:
        """The argument list the model is told (a JSON schema)."""
        return self._arguments

    @property
    def size(self) -> int:
        """How many characters of prompt this tool takes: its name, description and arguments."""
        return len(self.name) + len(self.description) + len(json.dumps(self._arguments))

    def _run(self, **arguments: Any) -> str:
        """Call the app's tool; return its result as text, or ``Error: ...``."""
        try:
            result = self._hub.call(self.server, self.remote_name, arguments)
        except Exception as error:
            logger.info("%s failed: %s", self.name, error)
            return f"Error: {error}"
        text = self._text(result)
        limit = self._hub.settings.max_result_chars
        if len(text) > limit:
            text = text[:limit].rstrip() + "\n[shortened]"
        notice = self._prompts.mcp_notice.format(server=self.server)
        return f"Error: {text}" if getattr(result, "is_error", False) else f"{notice}\n{text}"

    @staticmethod
    def _text(result: Any) -> str:
        """The text of a result: its text parts, or its structured content as JSON."""
        parts = [part.text for part in result.content if getattr(part, "type", "") == "text"]
        if parts:
            return "\n".join(parts)
        structured = getattr(result, "structured_content", None)
        if structured:
            return json.dumps(structured, ensure_ascii=False)
        return "[the result has no text]"


class McpTools:
    """Chooses which of the connected apps' tools the model is given, and builds them."""

    UNSAFE: ClassVar[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_-]")
    MAX_NAME: ClassVar[int] = 64

    KEEP_IN_SCHEMA: ClassVar[frozenset[str]] = frozenset(
        {"type", "properties", "required", "items", "enum", "anyOf", "oneOf", "allOf"}
    )
    MAX_ARGUMENT_NOTE: ClassVar[int] = 60

    @classmethod
    def compact(cls, schema: Any) -> Any:
        """A tool's argument list without what only costs prompt text.

        Keeps names, types, choices and which arguments are required; drops titles, defaults,
        examples and the long notes on arguments (the first ones are cut short instead).
        """
        if isinstance(schema, list):
            return [cls.compact(item) for item in schema]
        if not isinstance(schema, dict):
            return schema
        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                result[key] = {name: cls._argument(spec) for name, spec in value.items()}
            elif key in cls.KEEP_IN_SCHEMA:
                result[key] = cls.compact(value)
        return result

    @classmethod
    def _argument(cls, spec: Any) -> Any:
        compacted = cls.compact(spec)
        note = spec.get("description") if isinstance(spec, dict) else None
        if isinstance(compacted, dict) and isinstance(note, str) and note.strip():
            compacted["description"] = note.strip()[: cls.MAX_ARGUMENT_NOTE]
        return compacted

    @classmethod
    def create(
        cls,
        hub: McpHub,
        servers: list[McpServerConfig],
        settings: McpSettings,
        prompts: Prompts,
    ) -> list[BaseTool]:
        """The tools to give the model; the status records why each of the others is not given.

        Every tool the user ticked (``tools`` in ``mcp.json``) is given. An app with no ticks gets
        the tools marked read-only, or all of them if it is configured with ``allow_actions``.
        Nothing asks the user before a tool changes something, so those are never given unless
        the user chose them. Together the tools may take ``max_prompt_chars`` of prompt: a local
        model reads the prompt at about 250 tokens a second, so what does not fit is left out,
        and the status says so.
        """
        chosen: list[BaseTool] = []
        spent = 0
        statuses = {status.name: status for status in hub.status()}
        for server in servers:
            status = statuses.get(server.name)
            if status is None or not status.connected:
                continue
            status.offered, status.hidden = [], []  # built again for each agent: start afresh
            for remote in hub.tools(server.name):
                reason = cls._why_not(server, remote)
                tool = cls._tool(hub, server.name, remote, settings, prompts)
                if reason is None and spent + tool.size > settings.max_prompt_chars:
                    reason = (
                        f"left out: it needs {tool.size} characters of prompt and only "
                        f"{max(settings.max_prompt_chars - spent, 0)} are left"
                    )
                if reason is not None:
                    status.hidden.append((remote.name, reason))
                    continue
                spent += tool.size
                chosen.append(tool)
                status.offered.append(remote.name)
        return chosen

    @classmethod
    def listing(
        cls, hub: McpHub, server: McpServerConfig, settings: McpSettings, prompts: Prompts
    ) -> list[dict[str, Any]]:
        """Every tool of an app, for the Settings screen: what it does and whether it is on."""
        rows = []
        for remote in hub.tools(server.name):
            tool = cls._tool(hub, server.name, remote, settings, prompts)
            rows.append(
                {
                    "name": remote.name,
                    "description": tool.description,
                    "enabled": cls._why_not(server, remote) is None,
                    "changes_things": not (
                        remote.annotations is not None and remote.annotations.read_only_hint
                    ),
                    "chars": tool.size,
                }
            )
        return rows

    @classmethod
    def _tool(
        cls, hub: McpHub, server: str, remote: Tool, settings: McpSettings, prompts: Prompts
    ) -> McpTool:
        name = cls._name(server, remote.name)
        hint = settings.tool_hints.get(remote.name)
        description = (hint or remote.description or remote.name).strip()
        if not hint and len(description) > settings.max_description_chars:
            description = description[: settings.max_description_chars].rstrip() + "…"
        schema = cls.compact(remote.input_schema or {"type": "object", "properties": {}})
        return McpTool(hub, server, remote, prompts, name, description, schema)

    @staticmethod
    def _why_not(server: McpServerConfig, remote: Tool) -> str | None:
        """Why a tool is not offered, or ``None`` if it is."""
        if server.tools is not None:
            return None if remote.name in server.tools else "not ticked in Settings"
        if server.allow_actions:
            return None
        hints = remote.annotations
        if hints is not None and hints.read_only_hint:
            return None
        return "may change things: tick it in Settings to allow it"

    @classmethod
    def _name(cls, server: str, tool: str) -> str:
        """The name the model sees: ``app__tool``, without what a tool name cannot hold."""
        return cls.UNSAFE.sub("_", f"{server}{McpTool.SEPARATOR}{tool}")[: cls.MAX_NAME]


__all__ = ["McpTool", "McpTools", "ServerStatus"]
