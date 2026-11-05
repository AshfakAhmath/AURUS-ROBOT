"""Thread-safe SQLite persistence for identity, memory, and audit events."""

from __future__ import annotations

from contextlib import closing
import json
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

import numpy as np


class MemoryRepository:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._backup_legacy_database()
        self.initialize()

    def _backup_legacy_database(self) -> None:
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        backup = self.db_path.with_name(f"{self.db_path.stem}.legacy-backup{self.db_path.suffix}")
        if backup.exists():
            return
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                has_version = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
                ).fetchone()
            if not has_version:
                shutil.copy2(self.db_path, backup)
        except sqlite3.DatabaseError:
            shutil.copy2(self.db_path, backup)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def initialize(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS Users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    relationship_strength REAL DEFAULT 0.5
                );

                CREATE TABLE IF NOT EXISTS FaceEmbeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    vector BLOB NOT NULL,
                    dimensions INTEGER NOT NULL,
                    samples INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS Memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS Interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    interaction_type TEXT NOT NULL,
                    details TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES Users(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS Events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_memories_user_time ON Memories(user_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_interactions_time ON Interactions(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_events_time ON Events(timestamp DESC);
                """
            )
            row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (self.SCHEMA_VERSION,))
            elif row["version"] < self.SCHEMA_VERSION:
                conn.execute("UPDATE schema_version SET version = ?", (self.SCHEMA_VERSION,))
            conn.commit()

    def ensure_user(self, name: str) -> int:
        clean = " ".join(name.strip().split())[:80]
        if not clean:
            raise ValueError("name is required")
        with self._lock, closing(self._connect()) as conn:
            conn.execute("INSERT OR IGNORE INTO Users(name) VALUES (?)", (clean,))
            conn.execute("UPDATE Users SET last_seen = CURRENT_TIMESTAMP WHERE name = ?", (clean,))
            row = conn.execute("SELECT id FROM Users WHERE name = ?", (clean,)).fetchone()
            conn.commit()
            return int(row["id"])

    def get_user(self, user_id: int) -> dict | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT id, name, last_seen FROM Users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def save_embedding(self, user_id: int, vector: np.ndarray, samples: int) -> None:
        normalized = np.asarray(vector, dtype=np.float32).reshape(-1)
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO FaceEmbeddings(user_id, vector, dimensions, samples)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    vector = excluded.vector,
                    dimensions = excluded.dimensions,
                    samples = excluded.samples,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, normalized.tobytes(), normalized.size, samples),
            )
            conn.commit()

    def load_embeddings(self) -> list[tuple[int, str, np.ndarray]]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT u.id AS user_id, u.name, f.vector, f.dimensions
                FROM FaceEmbeddings f JOIN Users u ON u.id = f.user_id
                """
            ).fetchall()
        result = []
        for row in rows:
            vector = np.frombuffer(row["vector"], dtype=np.float32, count=row["dimensions"]).copy()
            result.append((int(row["user_id"]), row["name"], vector))
        return result

    def remember(self, user_id: int, content: str, memory_type: str = "fact") -> None:
        clean = " ".join(content.strip().split())[:500]
        if not clean:
            raise ValueError("memory content is required")
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO Memories(user_id, memory_type, content) VALUES (?, ?, ?)",
                (user_id, memory_type, clean),
            )
            conn.commit()

    def memories_for(self, user_id: int, limit: int = 8) -> list[str]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT content FROM Memories WHERE user_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
                (user_id, max(1, min(limit, 50))),
            ).fetchall()
            return [row["content"] for row in rows]

    def log_interaction(
        self,
        user_id: int | None,
        source: str,
        user_text: str,
        response: str,
        provider: str,
    ) -> None:
        details = json.dumps(
            {"source": source, "input": user_text[:1000], "response": response[:1000], "provider": provider},
            ensure_ascii=False,
        )
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO Interactions(user_id, interaction_type, details) VALUES (?, ?, ?)",
                (user_id, "conversation", details),
            )
            conn.commit()

    def log_event(self, event_type: str, description: str) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO Events(event_type, description) VALUES (?, ?)",
                (event_type[:80], description[:1000]),
            )
            conn.commit()

    def recent_interactions(self, limit: int = 6) -> Iterable[dict]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT details FROM Interactions ORDER BY timestamp DESC, id DESC LIMIT ?",
                (max(1, min(limit, 20)),),
            ).fetchall()
        for row in reversed(rows):
            try:
                yield json.loads(row["details"])
            except (TypeError, json.JSONDecodeError):
                continue
