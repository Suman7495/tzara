"""Conversation memory: what Tzara remembers between messages."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver


class ConversationMemory:
    """Holds one conversation's history, and the files the agent wrote in it.

    LangGraph stores a conversation as checkpoints under a *thread id*. Keeping
    the checkpointer and the thread id here, outside the agent, lets the history
    survive rebuilding the agent (after Ctrl+C) and switching models.

    The history lives in memory, so it ends with the process. Starting a new
    conversation drops it.
    """

    def __init__(self, checkpointer: InMemorySaver | None = None) -> None:
        """Start an empty conversation.

        Args:
            checkpointer: Where checkpoints are stored. Defaults to a new
                in-memory store.
        """
        self._checkpointer = checkpointer or InMemorySaver()
        self._thread_id = self._new_thread_id()

    @property
    def checkpointer(self) -> InMemorySaver:
        """The store to hand to ``create_deep_agent``."""
        return self._checkpointer

    @property
    def thread_id(self) -> str:
        """Identifies the current conversation."""
        return self._thread_id

    @property
    def run_config(self) -> RunnableConfig:
        """Run configuration that continues the current conversation."""
        return {"configurable": {"thread_id": self._thread_id}}

    def reset(self) -> None:
        """Forget the current conversation and start a new one."""
        self._checkpointer.delete_thread(self._thread_id)
        self._thread_id = self._new_thread_id()

    @contextmanager
    def scratch(self) -> Iterator[RunnableConfig]:
        """Provide a throwaway conversation that leaves no trace.

        Used for the warm-up message, which must not appear in the history.

        Yields:
            Run configuration for a conversation deleted on exit.
        """
        thread_id = self._new_thread_id()
        try:
            yield {"configurable": {"thread_id": thread_id}}
        finally:
            self._checkpointer.delete_thread(thread_id)

    @staticmethod
    def _new_thread_id() -> str:
        """Return a fresh, unique thread id."""
        return uuid.uuid4().hex
