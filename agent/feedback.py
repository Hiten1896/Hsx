from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any


DEFAULT_REDACTIONS = (
    re.compile(
        r"(?i)\b(?:api[_ -]?key|token|password|secret|authorization)\s*[:=]\s*"
        r"(?:bearer\s+)?[^\s,;]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)"),
    re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
)
_SECRET_FIELD_NAMES = {
    "accesskey",
    "apikey",
    "authorization",
    "bearertoken",
    "clientsecret",
    "password",
    "privatekey",
    "secret",
    "token",
}


def redact_text(value: str) -> str:
    result = str(value)
    for pattern in DEFAULT_REDACTIONS:
        result = pattern.sub("[REDACTED]", result)
    for source in os.getenv("HSX_FEEDBACK_REDACT_PATTERNS", "").split("||"):
        if not source.strip():
            continue
        try:
            result = re.sub(source, "[REDACTED]", result)
        except re.error:
            raise ValueError("HSX_FEEDBACK_REDACT_PATTERNS contains an invalid regular expression.")
    return result


class FeedbackStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                    reason TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    provider_calls TEXT NOT NULL DEFAULT '[]',
                    tool_names TEXT NOT NULL DEFAULT '[]',
                    app_version TEXT NOT NULL DEFAULT '',
                    client TEXT NOT NULL CHECK (client IN ('web', 'cli')),
                    prompt_messages TEXT NOT NULL DEFAULT '[]',
                    response TEXT NOT NULL DEFAULT '',
                    full_conversation TEXT NOT NULL DEFAULT '[]',
                    UNIQUE (conversation_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS feedback_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO feedback_settings(key, value)
                    VALUES ('save_feedback', '1'), ('include_full_conversation', '1'),
                           ('include_file_contents', '0');
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(feedback)")}
            if "provider_calls" not in columns:
                connection.execute(
                    "ALTER TABLE feedback ADD COLUMN provider_calls TEXT NOT NULL DEFAULT '[]'"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def settings(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = dict(connection.execute("SELECT key, value FROM feedback_settings").fetchall())
            count = connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        return {
            "save_feedback": rows.get("save_feedback", "1") == "1",
            "include_full_conversation": rows.get("include_full_conversation", "0") == "1",
            "include_file_contents": rows.get("include_file_contents", "0") == "1",
            "count": count,
            "storage": "Stored locally; chat and feedback history is summarized and pruned after 7 days. File contents are excluded.",
        }

    def update_settings(
        self,
        *,
        save_feedback: bool,
        include_full_conversation: bool,
        include_file_contents: bool = False,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO feedback_settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [
                    ("save_feedback", "1" if save_feedback else "0"),
                    ("include_full_conversation", "1" if include_full_conversation else "0"),
                    ("include_file_contents", "1" if include_file_contents else "0"),
                ],
            )
        return self.settings()

    def record(
        self,
        *,
        conversation_id: str,
        message_id: str,
        rating: str,
        reason: str = "",
        note: str = "",
        provider: str = "",
        model: str = "",
        provider_calls: list[dict[str, str]] | None = None,
        tool_names: list[str] | None = None,
        app_version: str = "",
        client: str = "web",
        prompt_messages: list[dict] | None = None,
        response: str = "",
        full_conversation: list[dict] | None = None,
    ) -> bool:
        if rating not in {"up", "down"}:
            raise ValueError("Feedback rating must be up or down.")
        if client not in {"web", "cli"}:
            raise ValueError("Feedback client must be web or cli.")
        if not conversation_id or not message_id:
            raise ValueError("Conversation and message ids are required.")
        settings = self.settings()
        if not settings["save_feedback"]:
            return False
        prompts = prompt_messages or []
        if not isinstance(prompts, list) or not all(isinstance(item, dict) for item in prompts):
            raise ValueError("Feedback prompt_messages must be a list of message objects.")
        prompts = _redact_value(prompts)
        calls = provider_calls or []
        if not isinstance(calls, list) or not all(isinstance(item, dict) for item in calls):
            raise ValueError("Feedback provider_calls must be a list of provider/model objects.")
        full = (
            _redact_value(full_conversation, include_data=settings["include_file_contents"])
            if settings["include_full_conversation"]
            else []
        )
        if not settings["include_file_contents"]:
            full = _without_file_contents(full)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO feedback (
                    created_at, conversation_id, message_id, rating, reason, note,
                    provider, model, provider_calls, tool_names, app_version, client,
                    prompt_messages, response, full_conversation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, message_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    rating = excluded.rating, reason = excluded.reason, note = excluded.note,
                    provider = excluded.provider, model = excluded.model,
                    provider_calls = excluded.provider_calls,
                    tool_names = excluded.tool_names, app_version = excluded.app_version,
                    client = excluded.client, prompt_messages = excluded.prompt_messages,
                    response = excluded.response, full_conversation = excluded.full_conversation""",
                (
                    datetime.now(UTC).isoformat(),
                    conversation_id[:100],
                    message_id[:100],
                    rating,
                    redact_text(reason[:200]),
                    redact_text(note[:2000]),
                    redact_text(provider[:100]),
                    redact_text(model[:200]),
                    json.dumps(_redact_value(calls), ensure_ascii=False),
                    json.dumps(tool_names or [], ensure_ascii=False),
                    app_version[:80],
                    client,
                    json.dumps(prompts, ensure_ascii=False),
                    redact_text(response[:100_000]),
                    json.dumps(full, ensure_ascii=False),
                ),
            )
        return True

    def delete_all(self) -> int:
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            connection.execute("DELETE FROM feedback")
        return count

    def prune_before(self, cutoff: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM feedback WHERE created_at < ?", (cutoff,))
        return cursor.rowcount

    def export_records(self, *, since: str | None = None, include_file_contents: bool = False) -> list[dict]:
        exact_cutoff = False
        if since:
            try:
                date.fromisoformat(since[:10])
            except ValueError as error:
                raise ValueError("The since date must use YYYY-MM-DD format.") from error
            exact_cutoff = len(since) > 10
        settings = self.settings()
        query = "SELECT * FROM feedback"
        parameters: tuple = ()
        if since:
            query += " WHERE created_at >= ?" if exact_cutoff else " WHERE substr(created_at, 1, 10) >= ?"
            parameters = (since,)
        query += " ORDER BY created_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        records = []
        for row in rows:
            prompt_messages = (
                json.loads(row["full_conversation"])
                if settings["include_full_conversation"]
                else json.loads(row["prompt_messages"])
            )
            if not (include_file_contents and settings["include_file_contents"]):
                prompt_messages = _without_file_contents(prompt_messages)
            records.append({
                "prompt_messages": _redact_value(
                    prompt_messages,
                    include_data=include_file_contents and settings["include_file_contents"],
                ),
                "response": redact_text(row["response"]),
                "rating": row["rating"],
                "reason": redact_text(row["reason"]),
                "note": redact_text(row["note"]),
                "metadata": _redact_value({
                    "conversation_id": row["conversation_id"],
                    "message_id": row["message_id"],
                    "provider": row["provider"],
                    "model": row["model"],
                    "provider_calls": json.loads(row["provider_calls"]),
                    "tool_names": json.loads(row["tool_names"]),
                    "app_version": row["app_version"],
                    "client": row["client"],
                }),
            })
        return records


def _redact_value(value, *, include_data: bool = False):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item, include_data=include_data) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _is_secret_field(key)
                else _redact_value(item, include_data=include_data)
            )
            for key, item in value.items()
            if include_data or key.lower() not in {
                "data",
                "file",
                "file_content",
                "file_contents",
                "attachment_content",
                "attachment_contents",
            }
        }
    return value


def _is_secret_field(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return normalized in _SECRET_FIELD_NAMES or normalized.endswith(
        ("apikey", "token", "password", "secret")
    )


def _without_file_contents(value):
    if isinstance(value, list):
        return [_without_file_contents(item) for item in value]
    if isinstance(value, dict):
        if str(value.get("role", "")).lower() in {"tool", "function"}:
            return {
                key: _without_file_contents(item)
                for key, item in value.items()
                if key.lower() in {"role", "name", "tool_name", "tool_call_id"}
            } | {"content": "[TOOL_RESULT_OMITTED]"}
        return {
            key: _without_file_contents(item)
            for key, item in value.items()
            if key.lower() not in {
                "data",
                "file",
                "file_content",
                "file_contents",
                "attachment_content",
                "attachment_contents",
            }
        }
    return value
