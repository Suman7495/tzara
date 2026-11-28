"""How the user writes: their own texts, kept to write like them."""

from __future__ import annotations

import time

from tzara.config import MemorySettings
from tzara.memory.database import MemoryDatabase
from tzara.memory.text import SearchText


class WritingSamples:
    """The user's own drafts, by kind of place (chat, email, social), newest last.

    Only text the user wrote themselves is kept, never what Tzara wrote or what was on the
    screen. A few of the latest are shown to the model when it writes in the user's place.
    """

    def __init__(self, database: MemoryDatabase, settings: MemorySettings) -> None:
        """Create the store.

        Args:
            database: Where samples are kept.
            settings: How many, and how long.
        """
        self._db = database
        self._settings = settings

    def add(self, place: str, text: str) -> bool:
        """Keep ``text`` as a sample for ``place``. Returns whether it was kept.

        Too short or already known texts are not kept; the oldest sample of the place goes when
        there are too many.
        """
        cleaned = text.strip()[: self._settings.max_sample_chars]
        if len(cleaned) < self._settings.min_sample_chars:
            return False
        key = SearchText.key(cleaned)
        for row in self._db.execute("SELECT text FROM samples WHERE place = ?", (place,)):
            if SearchText.key(row["text"]) == key:
                return False
        self._db.insert(
            "INSERT INTO samples(at, place, text) VALUES (?, ?, ?)", (time.time(), place, cleaned)
        )
        self._db.changed(
            "DELETE FROM samples WHERE place = ? AND id <= "
            "(SELECT id FROM samples WHERE place = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (place, place, self._settings.max_samples),
        )
        return True

    def recent(self, place: str, limit: int) -> list[str]:
        """The ``limit`` latest samples for ``place``, oldest first."""
        rows = self._db.execute(
            "SELECT text FROM samples WHERE place = ? ORDER BY id DESC LIMIT ?", (place, limit)
        )
        return [row["text"] for row in reversed(rows)]

    def all(self) -> list[tuple[str, str, float]]:
        """Every sample as (place, text, when), newest first."""
        rows = self._db.execute("SELECT place, text, at FROM samples ORDER BY id DESC")
        return [(row["place"], row["text"], row["at"]) for row in rows]

    def count(self) -> int:
        """How many samples are kept."""
        return int(self._db.execute("SELECT COUNT(*) AS n FROM samples")[0]["n"])

    def clear(self) -> int:
        """Forget them all. Returns how many there were."""
        return self._db.changed("DELETE FROM samples")
