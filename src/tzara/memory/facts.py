"""Semantic memory: lasting facts and preferences about the user."""

from __future__ import annotations

import time
from dataclasses import dataclass

from tzara.config import MemorySettings
from tzara.memory.database import MemoryDatabase
from tzara.memory.text import SearchText


class MemoryRefused(ValueError):
    """A fact was not stored. The message says why, in words a person or a model can act on."""


@dataclass(frozen=True)
class Fact:
    """One thing Tzara knows about the user.

    Attributes:
        id: Identifies the fact, so it can be deleted.
        text: The fact, as a short sentence.
        created_at: When it was first remembered, as a Unix time.
        updated_at: When it was last said again.
    """

    id: int
    text: str
    created_at: float
    updated_at: float


class FactStore:
    """Keeps facts and finds the ones that matter for what is being said."""

    COLUMNS = "facts.id, facts.text, facts.created_at, facts.updated_at"

    def __init__(self, database: MemoryDatabase, settings: MemorySettings) -> None:
        """Create the store.

        Args:
            database: Where facts are kept.
            settings: Limits.
        """
        self._db = database
        self._settings = settings

    def add(self, text: str, slot: str = "") -> tuple[Fact, bool]:
        """Remember ``text``.

        Saying the same thing again (in other capitals or punctuation) does not store it twice:
        the earlier fact is kept and marked as just confirmed.

        A fact with a ``slot`` (the user's name, employer, city...) replaces the earlier fact in
        that slot: a person who moves or changes jobs has one answer, not two that disagree.

        Returns:
            The fact, and whether it is new.

        Raises:
            MemoryRefused: If it is empty or too long, or the store is full.
        """
        cleaned = " ".join(text.split())
        key = SearchText.key(cleaned)
        if not key:
            raise MemoryRefused("There is nothing to remember in that.")
        if len(cleaned) > self._settings.max_fact_chars:
            raise MemoryRefused(
                f"That is too long to remember. Keep a fact to {self._settings.max_fact_chars} "
                "characters, one thing at a time."
            )
        now = time.time()
        replaced_at = self._forget_slot(slot, key)
        existing = self._db.execute(f"SELECT {self.COLUMNS} FROM facts WHERE key = ?", (key,))
        if existing:
            self._db.changed(
                "UPDATE facts SET updated_at = ? WHERE id = ?", (now, existing[0]["id"])
            )
            return self._fact(existing[0], updated_at=now), False
        if replaced_at is None and self.count() >= self._settings.max_facts:
            raise MemoryRefused(
                f"The memory is full ({self._settings.max_facts} facts). Forget something first."
            )
        created = now if replaced_at is None else replaced_at
        fact_id = self._db.insert(
            "INSERT INTO facts(text, key, created_at, updated_at, slot) VALUES (?, ?, ?, ?, ?)",
            (cleaned, key, created, now, slot),
        )
        return Fact(fact_id, cleaned, created, now), True

    def _forget_slot(self, slot: str, keep_key: str) -> float | None:
        """Delete the fact now in ``slot`` unless it is the one being said again.

        Returns when the replaced fact was first remembered, or ``None`` if nothing was replaced.
        """
        if not slot:
            return None
        rows = self._db.execute("SELECT id, key, created_at FROM facts WHERE slot = ?", (slot,))
        if not rows or rows[0]["key"] == keep_key:
            return None
        self._db.changed("DELETE FROM facts WHERE id = ?", (rows[0]["id"],))
        return float(rows[0]["created_at"])

    def by_slot(self, slot: str) -> Fact | None:
        """The fact in a slot (the user's city, employer...), if there is one."""
        rows = self._db.execute(f"SELECT {self.COLUMNS} FROM facts WHERE slot = ?", (slot,))
        return self._fact(rows[0]) if rows else None

    def remove(self, fact_id: int) -> bool:
        """Forget one fact. Returns whether there was one."""
        return self._db.changed("DELETE FROM facts WHERE id = ?", (fact_id,)) > 0

    def all(self) -> list[Fact]:
        """Every fact, the most recently confirmed first."""
        rows = self._db.execute(
            f"SELECT {self.COLUMNS} FROM facts ORDER BY updated_at DESC, id DESC"
        )
        return [self._fact(row) for row in rows]

    def count(self) -> int:
        """How many facts are stored."""
        return int(self._db.execute("SELECT COUNT(*) AS n FROM facts")[0]["n"])

    def search(self, query: str, limit: int) -> list[Fact]:
        """Facts that mention the words of ``query``, best match first."""
        match = SearchText.match(query)
        if match is None:
            return []
        rows = self._db.execute(
            f"SELECT {self.COLUMNS} FROM facts_fts JOIN facts ON facts.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? ORDER BY bm25(facts_fts) LIMIT ?",
            (match, limit),
        )
        return [self._fact(row) for row in rows]

    def recent(self, limit: int) -> list[Fact]:
        """The ``limit`` most recently confirmed facts."""
        return self.all()[:limit]

    def clear(self) -> int:
        """Forget everything. Returns how many facts there were."""
        return self._db.changed("DELETE FROM facts")

    @staticmethod
    def _fact(row, updated_at: float | None = None) -> Fact:  # type: ignore[no-untyped-def]
        """Build a :class:`Fact` from a database row."""
        return Fact(
            row["id"],
            row["text"],
            row["created_at"],
            row["updated_at"] if updated_at is None else updated_at,
        )
