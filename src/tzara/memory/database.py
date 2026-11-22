"""The SQLite file behind Tzara's memory."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar


class MemoryDatabase:
    """One SQLite connection, shared by every thread that needs memory.

    The server answers from several threads at once (a reply, the settings screen, a tool), so
    the connection is shared and every statement runs under a lock. The file is created readable
    by its owner only, because it holds what you said.

    Text search uses SQLite's FTS5, which ships with Python's ``sqlite3`` on every platform
    Tzara supports.
    """

    FILE_MODE: ClassVar[int] = 0o600
    DIRECTORY_MODE: ClassVar[int] = 0o700
    VERSION: ClassVar[int] = 2
    BUSY_TIMEOUT_MS: ClassVar[int] = 5000
    SCHEMA: ClassVar[str] = """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY,
            started_at REAL NOT NULL,
            ended_at REAL,
            title TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(text, content='messages', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS messages_added AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_removed AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
        CREATE TABLE IF NOT EXISTS profile (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            key TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
            USING fts5(text, content='facts', content_rowid='id');
        CREATE TRIGGER IF NOT EXISTS facts_added AFTER INSERT ON facts BEGIN
            INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS facts_removed AFTER DELETE ON facts BEGIN
            INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END;
    """

    def __init__(self, path: Path | None = None) -> None:
        """Open the database, creating it on first use.

        Args:
            path: The file. ``None`` keeps everything in memory, for tests.
        """
        self._lock = threading.RLock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=self.DIRECTORY_MODE)
            # Create the file owner-only before SQLite does, so it is never readable by others.
            os.close(os.open(path, os.O_RDWR | os.O_CREAT, self.FILE_MODE))
        self._connection = sqlite3.connect(
            ":memory:" if path is None else str(path), check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MS}")
            if path is not None:
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(self.SCHEMA)
            self._connection.execute(f"PRAGMA user_version = {self.VERSION}")
            self._connection.commit()

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run one statement, commit it, and return the rows it produced."""
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            rows = cursor.fetchall()
            self._connection.commit()
            return rows

    def insert(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        """Run an ``INSERT`` and return the new row's id."""
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            self._connection.commit()
            assert cursor.lastrowid is not None
            return cursor.lastrowid

    def changed(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        """Run an ``UPDATE`` or ``DELETE`` and return how many rows it changed."""
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            self._connection.commit()
            return cursor.rowcount

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._connection.close()
