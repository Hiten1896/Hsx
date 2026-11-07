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

    def prune_before(self, cutoff: str) -> int:
        cutoff_date = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
        if cutoff_date.tzinfo is None:
            cutoff_date = cutoff_date.replace(tzinfo=UTC)
        removed = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, thread_json, created FROM conversations"
            ).fetchall()
            for row in rows:
                thread = json.loads(row["thread_json"])
                messages = thread.get("messages", []) if isinstance(thread, dict) else []
                if not isinstance(messages, list):
                    messages = []
                if not messages and row["created"] >= cutoff:
                    continue
                retained = []
                for message in messages:
                    created = message.get("created_at") if isinstance(message, dict) else None
                    timestamp = created if isinstance(created, str) else row["created"]
                    try:
                        message_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except ValueError:
                        message_date = cutoff_date
                    if message_date.tzinfo is None:
                        message_date = message_date.replace(tzinfo=UTC)
                    if message_date >= cutoff_date:
                        retained.append(message)
                expired = len(messages) - len(retained)
                if expired:
                    thread["messages"] = retained
                if retained:
                    if expired:
                        connection.execute(
                            "UPDATE conversations SET thread_json = ? WHERE session_id = ?",
                            (json.dumps(thread, ensure_ascii=False, separators=(",", ":")), row["session_id"]),
                        )
                        removed += 1
                else:
                    cursor = connection.execute(
                        "DELETE FROM conversations WHERE session_id = ?", (row["session_id"],)
                    )
                    removed += cursor.rowcount
        return removed
