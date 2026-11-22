"""Episodic memory: what was said in past conversations."""

from __future__ import annotations

import time
from dataclasses import dataclass

from tzara.config import MemorySettings
from tzara.memory.database import MemoryDatabase
from tzara.memory.text import SearchText


@dataclass(frozen=True)
class StoredMessage:
    """One thing said in a stored conversation.

    Attributes:
        role: ``user`` or ``assistant``.
        text: What was said.
        at: When, as a Unix time.
    """

    role: str
    text: str
    at: float


@dataclass(frozen=True)
class Snippet:
    """A message found by searching past conversations.

    Attributes:
        conversation_id: The conversation it is from.
        title: What that conversation began with.
        at: When it was said, as a Unix time.
        role: ``user`` or ``assistant``.
        text: What was said, cut to a short excerpt.
    """

    conversation_id: int
    title: str
    at: float
    role: str
    text: str


class ConversationLog:
    """Keeps what the user and the assistant said, conversation by conversation.

    Only the visible text is kept: not the tool calls, the web pages the agent read, or its
    reasoning. That is enough to pick a conversation up again after a restart and to look things
    up later, and it keeps the file small and free of third-party page text.
    """

    TITLE_CHARS = 60

    def __init__(self, database: MemoryDatabase, settings: MemorySettings) -> None:
        """Create the log.

        Args:
            database: Where conversations are kept.
            settings: Limits.
        """
        self._db = database
        self._settings = settings

    def start(self) -> int:
        """Begin a new conversation and return its id."""
        return self._db.insert("INSERT INTO conversations(started_at) VALUES (?)", (time.time(),))

    def append(self, conversation_id: int, role: str, text: str) -> None:
        """Add a message. The first thing the user says becomes the conversation's title."""
        self._db.insert(
            "INSERT INTO messages(conversation_id, role, text, at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, text, time.time()),
        )
        if role == "user":
            title = " ".join(text.split())[: self.TITLE_CHARS]
            self._db.changed(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = ''",
                (title, conversation_id),
            )

    def finish(self, conversation_id: int) -> None:
        """Mark a conversation as over, so it is not picked up again at the next start."""
        self._db.changed(
            "UPDATE conversations SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (time.time(), conversation_id),
        )

    def latest_open(self) -> int | None:
        """The most recent conversation that was not ended and has something in it."""
        rows = self._db.execute(
            "SELECT id FROM conversations WHERE ended_at IS NULL "
            "AND EXISTS (SELECT 1 FROM messages WHERE conversation_id = conversations.id) "
            "ORDER BY id DESC LIMIT 1"
        )
        return int(rows[0]["id"]) if rows else None

    def messages(self, conversation_id: int) -> list[StoredMessage]:
        """The end of a conversation, oldest first, within ``restore_chars`` of text.

        A restored conversation has to fit the model's small context window, so it is the most
        recent part that is kept.
        """
        rows = self._db.execute(
            "SELECT role, text, at FROM messages WHERE conversation_id = ? ORDER BY id DESC",
            (conversation_id,),
        )
        kept: list[StoredMessage] = []
        used = 0
        for row in rows:
            used += len(row["text"])
            if used > self._settings.restore_chars and kept:
                break
            kept.append(StoredMessage(row["role"], row["text"], row["at"]))
        return kept[::-1]

    def search(self, query: str, limit: int, exclude: int | None = None) -> list[Snippet]:
        """Messages from past conversations that mention the words of ``query``.

        Args:
            query: What to look for.
            limit: Most results.
            exclude: A conversation to leave out, normally the one in progress, whose messages
                the model already has.
        """
        match = SearchText.match(query)
        if match is None:
            return []
        rows = self._db.execute(
            "SELECT messages.conversation_id, conversations.title, messages.at, messages.role, "
            "messages.text FROM messages_fts "
            "JOIN messages ON messages.id = messages_fts.rowid "
            "JOIN conversations ON conversations.id = messages.conversation_id "
            "WHERE messages_fts MATCH ? AND messages.conversation_id IS NOT ? "
            "ORDER BY bm25(messages_fts) LIMIT ?",
            (match, exclude, limit),
        )
        return [
            Snippet(
                row["conversation_id"],
                row["title"],
                row["at"],
                row["role"],
                self._excerpt(row["text"], SearchText.terms(query)),
            )
            for row in rows
        ]

    def count(self) -> int:
        """How many conversations are stored."""
        return int(self._db.execute("SELECT COUNT(*) AS n FROM conversations")[0]["n"])

    def clear(self) -> int:
        """Delete every conversation. Returns how many there were."""
        return self._db.changed("DELETE FROM conversations")

    def _excerpt(self, text: str, terms: list[str]) -> str:
        """The part of ``text`` around the first search word, cut to the snippet size."""
        flat = " ".join(text.split())
        limit = self._settings.snippet_chars
        if len(flat) <= limit:
            return flat
        lowered = flat.lower()
        hits = [lowered.find(term) for term in terms if term in lowered]
        start = max(min(hits, default=0) - limit // 4, 0)
        piece = flat[start : start + limit].strip()
        return ("…" if start else "") + piece + ("…" if start + limit < len(flat) else "")
