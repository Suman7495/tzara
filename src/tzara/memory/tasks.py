"""The last task the user asked for: "again", "the same for Ben", "what did you write?"."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from tzara.memory.database import MemoryDatabase


@dataclass(frozen=True)
class Task:
    """One thing the user asked Tzara to do, and what came of it.

    Attributes:
        kind: ``write`` (text put in a field of another app) or ``chat``.
        asked: What the user asked, in their words.
        app: The app it was for, if any.
        mode: How it was done (``guided``, ``edit``...), for a write.
        result: What Tzara wrote or answered (cut).
        at: When, as a Unix time.
    """

    kind: str
    asked: str
    app: str = ""
    mode: str = ""
    result: str = ""
    at: float = 0.0


class TaskMemory:
    """Keeps the latest task, in the file that holds the rest of memory."""

    KEY = "last_task"
    MAX_ASKED = 400
    MAX_RESULT = 3000

    def __init__(self, database: MemoryDatabase) -> None:
        """Create the store.

        Args:
            database: Where it is kept (the ``profile`` table).
        """
        self._db = database

    def remember(self, task: Task) -> None:
        """Make ``task`` the last one."""
        stored = Task(
            task.kind,
            " ".join(task.asked.split())[: self.MAX_ASKED],
            task.app,
            task.mode,
            task.result.strip()[: self.MAX_RESULT],
            task.at or time.time(),
        )
        self._db.changed(
            "INSERT INTO profile(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self.KEY, json.dumps(asdict(stored))),
        )

    def last(self, kind: str | None = None) -> Task | None:
        """The last task (of ``kind``, if given), or ``None``."""
        rows = self._db.execute("SELECT value FROM profile WHERE key = ?", (self.KEY,))
        if not rows:
            return None
        task = Task(**json.loads(rows[0]["value"]))
        return task if kind is None or task.kind == kind else None

    def clear(self) -> None:
        """Forget it."""
        self._db.changed("DELETE FROM profile WHERE key = ?", (self.KEY,))
