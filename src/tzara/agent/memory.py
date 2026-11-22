"""Conversation memory: what Tzara remembers between messages."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from tzara.memory import ConversationLog


@dataclass(frozen=True)
class HistoryMessage:
    """One thing said in the conversation.

    Attributes:
        role: ``user`` or ``assistant``.
        text: What was said.
    """

    role: str
    text: str


class ConversationMemory:
    """Holds one conversation's history, and the files the agent wrote in it.

    LangGraph stores a conversation as checkpoints under a *thread id*. Keeping
    the checkpointer and the thread id here, outside the agent, lets the history
    survive rebuilding the agent (after Ctrl+C) and switching models.

    The checkpoints live in memory and end with the process. With a
    :class:`ConversationLog`, what was said is also written down, so that after a
    restart the conversation that was in progress is restored (see :meth:`take_restore`)
    and past conversations can be searched. Starting a new conversation ends the one
    in the log and drops the checkpoints.
    """

    def __init__(
        self, checkpointer: InMemorySaver | None = None, log: ConversationLog | None = None
    ) -> None:
        """Start a conversation, continuing the one in progress if a log has it.

        Args:
            checkpointer: Where checkpoints are stored. Defaults to a new
                in-memory store.
            log: Where what is said is written down. ``None`` keeps nothing on disk.
        """
        self._checkpointer = checkpointer or InMemorySaver()
        self._thread_id = self._new_thread_id()
        self._log = log
        self._conversation_id: int | None = None
        self._restore: list[HistoryMessage] = []
        if log is not None and (latest := log.latest_open()) is not None:
            self._conversation_id = latest
            self._restore = [HistoryMessage(m.role, m.text) for m in log.messages(latest)]

    @property
    def checkpointer(self) -> InMemorySaver:
        """The store to hand to ``create_agent``."""
        return self._checkpointer

    @property
    def thread_id(self) -> str:
        """Identifies the current conversation."""
        return self._thread_id

    @property
    def run_config(self) -> RunnableConfig:
        """Run configuration that continues the current conversation."""
        return {"configurable": {"thread_id": self._thread_id}}

    @property
    def conversation_id(self) -> int | None:
        """The current conversation's id in the log, or ``None`` before anything is said."""
        return self._conversation_id

    def take_restore(self) -> list[HistoryMessage]:
        """Return the messages of the conversation found in the log at start, once.

        The agent puts them back into the checkpoint, so it carries on where it left off.
        """
        restored, self._restore = self._restore, []
        return restored

    def record(self, role: str, text: str) -> None:
        """Write down something that was said, so it survives a restart."""
        if self._log is None or not text.strip():
            return
        if self._conversation_id is None:
            self._conversation_id = self._log.start()
        self._log.append(self._conversation_id, role, text)

    def reset(self) -> None:
        """Forget the current conversation and start a new one."""
        self._checkpointer.delete_thread(self._thread_id)
        self._thread_id = self._new_thread_id()
        if self._log is not None and self._conversation_id is not None:
            self._log.finish(self._conversation_id)
        self._conversation_id = None
        self._restore = []

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
