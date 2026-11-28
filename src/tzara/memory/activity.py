"""Episodic memory of what Tzara did for the user: a page it changed, a message it wrote."""

from __future__ import annotations

import time
from dataclasses import dataclass

from tzara.config import MemorySettings
from tzara.memory.database import MemoryDatabase
from tzara.memory.text import SearchText


@dataclass(frozen=True)
class Activity:
    """One thing Tzara did.

    Attributes:
        at: When, as a Unix time.
        kind: ``app`` (a tool of a connected app), ``write`` (text put into another app's field).
        summary: What it was, in one line, with the result.
    """

    at: float
    kind: str
    summary: str


class ActivityLog:
    """Keeps what was done, so it is known after the conversation moves on or Tzara restarts."""

    def __init__(self, database: MemoryDatabase, settings: MemorySettings) -> None:
        """Create the log.

        Args:
            database: Where it is kept.
            settings: How much is kept and how far back "recent" goes.
        """
        self._db = database
        self._settings = settings

    def record(self, kind: str, summary: str) -> None:
        """Note that something was done. The oldest entries go once there are too many."""
        line = " ".join(summary.split())[:300]
        if not line:
            return
        self._db.insert(
            "INSERT INTO activity(at, kind, summary) VALUES (?, ?, ?)", (time.time(), kind, line)
        )
        self._db.changed(
            "DELETE FROM activity WHERE id <= "
            "(SELECT id FROM activity ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (self._settings.max_activity,),
        )

    def recent(self, limit: int | None = None) -> list[Activity]:
        """The latest entries within the recent window, oldest first."""
        limit = limit or self._settings.inject_activity
        since = time.time() - self._settings.activity_window_seconds
        rows = self._db.execute(
            "SELECT at, kind, summary FROM activity WHERE at >= ? ORDER BY id DESC LIMIT ?",
            (since, limit),
        )
        return [Activity(row["at"], row["kind"], row["summary"]) for row in reversed(rows)]

    def latest(self, limit: int) -> list[Activity]:
        """The newest entries, newest first."""
        rows = self._db.execute(
            "SELECT at, kind, summary FROM activity ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [Activity(row["at"], row["kind"], row["summary"]) for row in rows]

    def search(self, query: str, limit: int) -> list[Activity]:
        """Entries that mention the words of ``query``, newest first."""
        terms = SearchText.terms(query)
        if not terms:
            return []
        clause = " OR ".join("summary LIKE ?" for _ in terms)
        rows = self._db.execute(
            f"SELECT at, kind, summary FROM activity WHERE {clause} ORDER BY id DESC LIMIT ?",
            (*[f"%{term}%" for term in terms], limit),
        )
        return [Activity(row["at"], row["kind"], row["summary"]) for row in rows]

    def count(self) -> int:
        """How many entries are kept."""
        return int(self._db.execute("SELECT COUNT(*) AS n FROM activity")[0]["n"])

    def clear(self) -> int:
        """Forget all of it. Returns how many entries there were."""
        return self._db.changed("DELETE FROM activity")
