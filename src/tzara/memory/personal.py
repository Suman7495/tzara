"""All of Tzara's long-term memory, opened together."""

from __future__ import annotations

from pathlib import Path

from tzara.config import MemorySettings
from tzara.memory.activity import ActivityLog
from tzara.memory.capture import FactCapture
from tzara.memory.database import MemoryDatabase
from tzara.memory.episodes import ConversationLog
from tzara.memory.facts import FactStore
from tzara.memory.styles import WritingSamples


class PersonalMemory:
    """The facts Tzara knows about the user and the conversations it has had, in one file."""

    FILE_NAME = "memory.db"

    def __init__(self, database: MemoryDatabase, settings: MemorySettings) -> None:
        """Create the memory on an open database.

        Args:
            database: Where everything is kept.
            settings: Limits.
        """
        self._database = database
        self.settings = settings
        self.facts = FactStore(database, settings)
        self.log = ConversationLog(database, settings)
        self.capture = FactCapture(self.facts, settings)
        self.activity = ActivityLog(database, settings)
        self.styles = WritingSamples(database, settings)

    @classmethod
    def open(cls, settings: MemorySettings, directory: Path | None) -> PersonalMemory | None:
        """Open the memory in ``directory``, or return ``None`` when memory is turned off.

        Args:
            settings: Memory settings.
            directory: Tzara's data directory. ``None`` keeps the memory in RAM (tests).
        """
        if not settings.enabled:
            return None
        path = None if directory is None else directory / cls.FILE_NAME
        return cls(MemoryDatabase(path), settings)

    def erase(self) -> None:
        """Forget everything: facts, conversations, what was done, how the user writes."""
        self.facts.clear()
        self.log.clear()
        self.activity.clear()
        self.styles.clear()

    def close(self) -> None:
        """Close the database."""
        self._database.close()
