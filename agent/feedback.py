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
        r"(?i)(\b(?:api[_ -]?key|token|password|secret|authorization|username|user[_ -]?name|"
        r"login|otp|one[_ -]?time[_ -]?(?:password|code)|verification[_ -]?code|"
        r"mobile|phone|account[_ -]?(?:number|no)|card[_ -]?(?:number|no)|"
        r"credit[_ -]?card|cvv|cvc|ssn)\s*(?::|=|\bis\b)\s*)"
        r"(?:bearer\s+)?(?!\[REDACTED\])[^\s,;]+"
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
    "username",
    "mobile",
    "mobilenumber",
    "phone",
    "phonenumber",
    "accountnumber",
    "cardnumber",
    "creditcard",
    "otp",
    "cvv",
    "cvc",
    "ssn",
}
_FILE_TOOLS = {
    "create_text_file",
    "make_csv",
    "make_docx",
    "make_markdown",
    "make_pdf",
    "make_pptx",
    "make_website",
    "report",
}
_CAMERA_TOOLS = {"camera_snapshot", "camera_record", "vision_capture"}
_FILE_CONTENT_KEYS = {
    "attachment_content",
    "attachment_contents",
    "code",
    "content",
    "data",
    "file",
    "file_content",
    "file_contents",
    "implementation",
    "script",
    "source",
    "source_code",
    "text",
}


def redact_text(value: str) -> str:
    result = str(value)
    result = DEFAULT_REDACTIONS[0].sub(r"\1[REDACTED]", result)
    for pattern in DEFAULT_REDACTIONS[1:]:
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
            if "context_metadata" not in columns:
                connection.execute(
                    "ALTER TABLE feedback ADD COLUMN context_metadata TEXT NOT NULL DEFAULT '{}'"
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
            "storage": (
                "Stored locally; chat and feedback history is summarized and pruned after 7 days. "
                + (
                    "File contents are included when requested."
                    if rows.get("include_file_contents", "0") == "1"
                    else "File contents are excluded."
                )
            ),
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
        context_metadata: dict[str, Any] | None = None,
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
        context = context_metadata or {}
        if not isinstance(context, dict):
            raise ValueError("Feedback context_metadata must be an object.")
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
                    prompt_messages, response, full_conversation, context_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, message_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    rating = excluded.rating, reason = excluded.reason, note = excluded.note,
                    provider = excluded.provider, model = excluded.model,
                    provider_calls = excluded.provider_calls,
                    tool_names = excluded.tool_names, app_version = excluded.app_version,
                    client = excluded.client, prompt_messages = excluded.prompt_messages,
                    response = excluded.response, full_conversation = excluded.full_conversation,
                    context_metadata = excluded.context_metadata""",
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
                    json.dumps(_redact_value(context), ensure_ascii=False),
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
                    "context": _redact_value(json.loads(row["context_metadata"])),
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
        if value.get("type") == "tool_result":
            return {
                key: _without_file_contents(item)
                for key, item in value.items()
                if key != "content"
            } | {"content": "[TOOL_RESULT_OMITTED]"}
        if value.get("type") == "tool_use" and isinstance(value.get("input"), dict):
            return {
                **{
                    key: _without_file_contents(item)
                    for key, item in value.items()
                    if key != "input"
                },
                "input": _without_tool_input_contents(value["input"]),
            }
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


def _without_tool_input_contents(value):
    if isinstance(value, list):
        return [_without_tool_input_contents(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _without_tool_input_contents(item)
            for key, item in value.items()
            if key.casefold() not in _FILE_CONTENT_KEYS
        }
    return value


def feedback_context_metadata(
    request: str,
    history: list[dict] | None,
    tool_names: list[str] | None,
) -> dict[str, Any]:
    tools = set(tool_names or [])
    uploaded_files = set()
    generated_files = set()
    created_tools = set()
    camera_references = set()
    for line in request.splitlines():
        candidate = line.strip().removeprefix("- ").replace("\\", "/")
        if candidate.startswith(("Uploads/", "Browser Media/")):
            uploaded_files.add(redact_text(candidate)[:500])
    for message in history or []:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            arguments = block.get("input")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                continue
            tools.add(name)
            path_values = [
                arguments[key]
                for key in ("filename", "path", "file_path", "output_path")
                if isinstance(arguments.get(key), str) and arguments[key].strip()
            ]
            if name in _FILE_TOOLS:
                generated_files.update(redact_text(path.replace("\\", "/"))[:500] for path in path_values)
            if name in _CAMERA_TOOLS:
                camera_references.update(redact_text(path.replace("\\", "/"))[:500] for path in path_values)
            if name in {"tool_creator", "create_dynamic_tool"}:
                created_name = arguments.get("name")
                if isinstance(created_name, str) and created_name.strip():
                    created_tools.add(redact_text(created_name.strip())[:100])
    return {
        "uploaded_files": sorted(uploaded_files),
        "generated_files": sorted(generated_files),
        "tools_used": sorted(tools),
        "tools_created": sorted(created_tools),
        "camera_capture_references": sorted(camera_references),
        "camera_footage_retained": False,
    }
