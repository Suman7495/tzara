"""Telling the model what it remembers about the user."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from tzara.config import MemorySettings
from tzara.memory import ActivityLog, FactStore
from tzara.prompts import Prompts


class MemoryMiddleware(AgentMiddleware):
    """Adds the facts that matter to the system prompt before each model call.

    The system prompt is fixed when the agent is built, but what is remembered changes while
    the conversation goes on, so the facts are added as the request is made. Only the most
    recent are added, within a few hundred characters, because the context window is small, and
    always the same ones until something changes.
    """

    def __init__(
        self,
        facts: FactStore,
        settings: MemorySettings,
        prompts: Prompts,
        activity: ActivityLog | None = None,
    ) -> None:
        """Create the middleware.

        Args:
            facts: Where facts are kept.
            settings: How many facts and how many characters to add.
            prompts: Where the wording around the facts comes from.
            activity: What Tzara did lately, told to the model too. ``None``: not told.
        """
        super().__init__()
        self._activity = activity
        self._facts = facts
        self._settings = settings
        self._prompts = prompts

    def block(self) -> str:
        """The text to add to the system prompt, or empty if nothing is known.

        It is the same from one message to the next, whatever is being said, until a fact
        changes: a prompt that changes makes the model redo its work on all of it.
        """
        lines: list[str] = []
        used = 0
        # In a fixed order, so the same facts always give the same text: Ollama reuses the
        # work it did on the start of the last prompt only if that start has not changed.
        for fact in sorted(self._facts.recent(self._settings.inject_facts), key=lambda f: f.id):
            if used + len(fact.text) > self._settings.inject_chars:
                break
            lines.append(f"- {fact.text}")
            used += len(fact.text)
        text = self._prompts.memory_block.format(facts="\n".join(lines)) if lines else ""
        return text + self._activity_block()

    def _activity_block(self) -> str:
        """What was done lately, one line each, with the time it was done (a fixed text)."""
        if self._activity is None:
            return ""
        lines = [
            f"- {time.strftime('%b %d %H:%M', time.localtime(item.at))}: {item.summary}"
            for item in self._activity.recent()
        ]
        return self._prompts.activity_block.format(lines="\n".join(lines)) if lines else ""

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> Any:
        """Add what is remembered to this request's system prompt, then carry on."""
        block = self.block()
        if block:
            current = request.system_message.text if request.system_message else ""
            request = request.override(system_message=SystemMessage(content=current + block))
        return handler(request)
