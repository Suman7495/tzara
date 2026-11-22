"""Telling the user what the agent is doing while it works."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from tzara.agent.events import Progress, ToolCompleted, ToolStarted
from tzara.agent.links import TrustedLinks
from tzara.prompts import Prompts


class SafeFields(dict[str, str]):
    """Template fields where a missing one reads as empty instead of failing."""

    def __missing__(self, key: str) -> str:
        """Return nothing for a field the tool did not have."""
        return ""


class Activity(BaseCallbackHandler):
    """Reports each tool the agent starts and finishes, in words a person understands.

    A reply can take many seconds when the agent searches the web, and a spinner alone looks
    like a hang. This announces ``Searching the web for "..."`` the moment the tool starts, and
    that it has finished. Tools that do several steps (research) report their own steps through
    :meth:`report`.

    Events go to a listener that the current reply sets, and are forgotten when it ends. The
    tools that read the web are noted too: see :attr:`read_the_web`.
    """

    MAX_FIELD_CHARS = 60
    WEB_TOOLS = frozenset({"web_search", "read_webpage", "research"})

    def __init__(self, prompts: Prompts) -> None:
        """Create the reporter.

        Args:
            prompts: Where the wording for each tool comes from.
        """
        self._prompts = prompts
        self._listener: Callable[[ToolStarted | ToolCompleted | Progress], None] | None = None
        self._running: dict[UUID, str] = {}
        self._read_the_web = False
        self.links = TrustedLinks()

    @property
    def read_the_web(self) -> bool:
        """Whether this turn has seen text that may not be the user's own words.

        Set when a tool that reads web pages has run, or when text from the user's screen came
        with the message (:meth:`distrust`). Such text can hold instructions, so what the agent
        does afterwards in that turn (such as remembering something) must not be taken as the
        user's wish.
        """
        return self._read_the_web

    def distrust(self) -> None:
        """Note that text from the user's screen came with this turn: it may hold instructions."""
        self._read_the_web = True

    def begin_turn(self) -> None:
        """Forget what the last reply did: a new reply starts with nothing read or seen."""
        self._read_the_web = False
        self._running.clear()
        self.links.clear()

    def listen(
        self, listener: Callable[[ToolStarted | ToolCompleted | Progress], None] | None
    ) -> None:
        """Send events to ``listener``, or to nobody when ``None``."""
        self._listener = listener

    def report(self, text: str) -> None:
        """Tell the listener, if there is one, about a step inside a tool."""
        self._emit(Progress(text))

    def _emit(self, event: ToolStarted | ToolCompleted | Progress) -> None:
        """Pass ``event`` to the listener, if there is one."""
        listener = self._listener
        if listener is not None:
            listener(event)

    def describe(self, tool: str, arguments: dict[str, Any]) -> str:
        """Return the sentence for starting ``tool`` with ``arguments``."""
        template = self._prompts.tool_activity.get(tool, self._prompts.default_activity)
        return template.format_map(self.fields(arguments))

    def fields(self, arguments: dict[str, Any]) -> SafeFields:
        """Turn tool arguments into short pieces of text a template can use.

        A ``url`` also gives ``host``, and a ``path`` also gives its file name as ``name``.
        """
        fields = SafeFields()
        for key, value in arguments.items():
            fields[key] = self._short(str(value))
        if "url" in arguments:
            host = urlparse(str(arguments["url"])).hostname or str(arguments["url"])
            fields["host"] = self._short(host.removeprefix("www."))
        if "path" in arguments:
            fields["name"] = self._short(os.path.basename(str(arguments["path"]).rstrip("/")))
        return fields

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """Announce a tool as it starts."""
        inputs = kwargs.get("inputs")
        arguments = inputs if isinstance(inputs, dict) else {}
        name = str((serialized or {}).get("name") or kwargs.get("name") or "")
        if name in self.WEB_TOOLS:
            self._read_the_web = True
        run_id = kwargs.get("run_id")
        if isinstance(run_id, UUID):
            self._running[run_id] = name
        self._emit(ToolStarted(name, self.describe(name, arguments)))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Announce that a tool has finished, and note the web addresses it returned."""
        self.links.collect(str(getattr(output, "content", output)))
        self._emit(ToolCompleted(self._finished(kwargs.get("run_id"))))

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """Announce that a tool has failed."""
        self._emit(ToolCompleted(self._finished(kwargs.get("run_id")), failed=True))

    def _finished(self, run_id: object) -> str:
        """The name of the tool whose run ended, or empty if it was not noticed starting."""
        return self._running.pop(run_id, "") if isinstance(run_id, UUID) else ""

    def _short(self, text: str) -> str:
        """Cut ``text`` to one short line."""
        line = " ".join(text.split())
        if len(line) <= self.MAX_FIELD_CHARS:
            return line
        return line[: self.MAX_FIELD_CHARS - 1].rstrip() + "…"
