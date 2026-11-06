"""Persistent browser conversation snapshots."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class ConversationStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY, title TEXT NOT NULL, thread_json TEXT NOT NULL,
                created TEXT NOT NULL, updated TEXT NOT NULL)"""
            )

    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, session_id: str, title: str, thread: dict) -> None:
        if not session_id or not isinstance(thread, dict):
            raise ValueError("A session ID and conversation thread are required.")
        now = datetime.now(UTC).isoformat()
        serialized = json.dumps(thread, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO conversations(session_id, title, thread_json, created, updated)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET
                title=excluded.title, thread_json=excluded.thread_json, updated=excluded.updated""",
                (session_id[:100], title.strip()[:100] or "New chat", serialized, now, now),
            )

    def list(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, title, created, updated FROM conversations ORDER BY updated DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, session_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_id, title, thread_json, created, updated FROM conversations WHERE session_id=?",
                (session_id[:100],),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["thread"] = json.loads(result.pop("thread_json"))
        return result

    def rename(self, session_id: str, title: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE conversations SET title=?, updated=? WHERE session_id=?",
                (title.strip()[:100] or "New chat", datetime.now(UTC).isoformat(), session_id[:100]),
            )
        return cursor.rowcount > 0

    def delete(self, session_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM conversations WHERE session_id=?", (session_id[:100],))
        return cursor.rowcount > 0
