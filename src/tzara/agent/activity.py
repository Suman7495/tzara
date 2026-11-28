"""Telling the user what the agent is doing while it works."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from tzara.agent.actions import ActionRecord
from tzara.agent.events import Progress, ToolCompleted, ToolStarted
from tzara.agent.links import TrustedLinks
from tzara.mcp.tools import McpTool
from tzara.prompts import Prompts

logger = logging.getLogger(__name__)


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
    TARGET_ARGUMENTS = ("page_id", "id", "url", "path", "name", "title", "query", "question")
    MAX_LOGGED_VALUE = 80
    WORDS_OF_THE_USER = frozenset({"query", "question", "title", "text", "content", "message"})
    MAX_LOGGED_ERROR = 300
    WEB_TOOLS = frozenset({"web_search", "read_webpage", "research"})

    def __init__(self, prompts: Prompts) -> None:
        """Create the reporter.

        Args:
            prompts: Where the wording for each tool comes from.
        """
        self._prompts = prompts
        self._listener: Callable[[ToolStarted | ToolCompleted | Progress], None] | None = None
        self._running: dict[UUID, str] = {}
        self._begun: dict[UUID, tuple[float, str]] = {}
        self.actions: list[ActionRecord] = []
        self._read_the_web = False
        self.tools_run: set[str] = set()
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
        self.tools_run.clear()
        self.actions.clear()
        self._begun.clear()
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
        known = self._prompts.tool_activity.get(tool)
        if known is None and McpTool.SEPARATOR in tool:
            server, _, name = tool.partition(McpTool.SEPARATOR)
            return self._prompts.mcp_activity.format(server=server, tool=name.replace("_", " "))
        template = known or self._prompts.default_activity
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
        # What a web page or a connected app returns is data from outside, which may hold
        # instructions: nothing is remembered on its say.
        if name in self.WEB_TOOLS or McpTool.SEPARATOR in name:
            self._read_the_web = True
        self.tools_run.add(name)
        run_id = kwargs.get("run_id")
        if isinstance(run_id, UUID):
            self._running[run_id] = name
            self._begun[run_id] = (time.monotonic(), self._target(arguments))
        logger.info("tool started: %s %s", name, self._described(arguments))
        self._emit(ToolStarted(name, self.describe(name, arguments)))

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Announce that a tool has finished, and note the web addresses it returned."""
        text = str(getattr(output, "content", output))
        self.links.collect(text)
        failed = text.startswith("Error")
        name = self._finished_note(
            kwargs.get("run_id"), "failed: " + text[:100] if failed else "ok"
        )
        logger.info(
            "tool finished: %s %s, %d characters back%s",
            name,
            "with an error" if failed else "ok",
            len(text),
            f": {self._short_error(text)}" if failed else "",
        )
        logger.debug("tool result of %s: %s", name, text[:1000])
        self._emit(ToolCompleted(self._finished(kwargs.get("run_id"))))

    def on_llm_end(self, response: Any, **_kwargs: Any) -> None:
        """Log how long the model took to read the prompt and to write: where a slow reply went."""
        try:
            message = response.generations[0][0].message
            info = message.response_metadata
        except (AttributeError, IndexError, TypeError):
            return
        if not str(message.content).strip() and not message.tool_calls:
            logger.warning(
                "the model returned nothing to show: invalid tool calls %s",
                [
                    (call.get("name"), str(call.get("error"))[:120])
                    for call in message.invalid_tool_calls
                ],
            )
        if message.tool_calls:
            logger.info(
                "the model asked for: %s", [call.get("name") for call in message.tool_calls]
            )
        if "prompt_eval_count" not in info:
            return
        logger.info(
            "model call: read %d tokens in %.1fs, wrote %d tokens in %.1fs",
            info.get("prompt_eval_count", 0),
            info.get("prompt_eval_duration", 0) / 1e9,
            info.get("eval_count", 0),
            info.get("eval_duration", 0) / 1e9,
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """Announce that a tool has failed."""
        name = self._finished_note(kwargs.get("run_id"), f"failed: {error}"[:100])
        logger.warning("tool failed: %s: %s", name, str(error)[: self.MAX_LOGGED_ERROR])
        self._emit(ToolCompleted(self._finished(kwargs.get("run_id")), failed=True))

    def _finished_note(self, run_id: object, result: str) -> str:
        """Record how a tool ended (for the reply's action note and the log); return its name."""
        if not isinstance(run_id, UUID):
            return ""
        name = self._running.get(run_id, "")
        began, target = self._begun.pop(run_id, (time.monotonic(), ""))
        if name:
            self.actions.append(ActionRecord(name, target, result))
            logger.info("tool took %.1fs: %s", time.monotonic() - began, name)
        return name

    def _target(self, arguments: dict[str, Any]) -> str:
        """What a tool call is about, short: its page, address, file or search."""
        for key in self.TARGET_ARGUMENTS:
            if arguments.get(key):
                return self._short(str(arguments[key]))
        return ""

    def _described(self, arguments: dict[str, Any]) -> str:
        """The arguments for the log: short values as they are, long text only by its length.

        Message and page text are not logged at the default level, so a long value (a paragraph
        the model wrote) is shown by its size.
        """
        parts = []
        for key, value in arguments.items():
            text = str(value)
            parts.append(
                f"{key}={' '.join(text.split())}"
                if len(text) <= self.MAX_LOGGED_VALUE and key not in self.WORDS_OF_THE_USER
                else f"{key}=<{len(text)} characters>"
            )
        return ", ".join(parts) or "(no arguments)"

    def _short_error(self, text: str) -> str:
        return " ".join(text.split())[: self.MAX_LOGGED_ERROR]

    def _finished(self, run_id: object) -> str:
        """The name of the tool whose run ended, or empty if it was not noticed starting."""
        return self._running.pop(run_id, "") if isinstance(run_id, UUID) else ""

    def _short(self, text: str) -> str:
        """Cut ``text`` to one short line."""
        line = " ".join(text.split())
        if len(line) <= self.MAX_FIELD_CHARS:
            return line
        return line[: self.MAX_FIELD_CHARS - 1].rstrip() + "…"
