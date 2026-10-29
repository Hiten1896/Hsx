from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


class AskUserRequest(Exception):
    def __init__(
        self,
        question: str,
        options: list[str] | None = None,
        allow_free_text: bool = True,
        timeout_hours: int = 24,
    ):
        question = question.strip()
        if not question or len(question) > 500:
            raise ValueError("The question must contain 1 to 500 characters.")
        if options is not None and (
            not isinstance(options, list)
            or not options
            or len(options) > 10
            or any(not isinstance(option, str) or not option.strip() or len(option) > 200 for option in options)
        ):
            raise ValueError("Provide up to 10 non-empty options, each no longer than 200 characters.")
        if not isinstance(timeout_hours, int) or timeout_hours < 1:
            raise ValueError("timeout_hours must be a positive integer.")
        self.question = question
        self.options = [option.strip() for option in options] if options else []
        self.allow_free_text = bool(allow_free_text)
        self.timeout_hours = timeout_hours
        self.checkpoint: list[dict] = []
        self.tool_call_id = ""


class AskUserJobs:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._stop_expirer = threading.Event()
        self._expirer: threading.Thread | None = None
        self._initialize()
        self.expire()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS ask_jobs (
                id TEXT PRIMARY KEY, request TEXT NOT NULL, session_id TEXT NOT NULL,
                question TEXT NOT NULL, options TEXT NOT NULL, allow_free_text INTEGER NOT NULL,
                state TEXT NOT NULL, question_count INTEGER NOT NULL, answers TEXT NOT NULL,
                checkpoint TEXT NOT NULL, tool_call_id TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', task_dir TEXT NOT NULL DEFAULT '',
                target_device TEXT NOT NULL DEFAULT 'client_browser',
                device_actions TEXT NOT NULL DEFAULT '[]',
                created TEXT NOT NULL, updated TEXT NOT NULL,
                expires_at TEXT NOT NULL)"""
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(ask_jobs)")}
            if "task_dir" not in columns:
                connection.execute("ALTER TABLE ask_jobs ADD COLUMN task_dir TEXT NOT NULL DEFAULT ''")
            if "target_device" not in columns:
                connection.execute(
                    "ALTER TABLE ask_jobs ADD COLUMN target_device TEXT NOT NULL DEFAULT 'client_browser'"
                )
            if "device_actions" not in columns:
                connection.execute(
                    "ALTER TABLE ask_jobs ADD COLUMN device_actions TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute(
                """UPDATE ask_jobs SET state = 'failed',
                error = 'The server restarted while this job was running; submit it again.',
                updated = ? WHERE state = 'running'""",
                (self._now().isoformat(),),
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        job = dict(row)
        for field in ("options", "answers", "checkpoint", "device_actions", "result"):
            job[field] = json.loads(job[field]) if job[field] else ([] if field in {"options", "answers", "checkpoint", "device_actions"} else None)
        job["allow_free_text"] = bool(job["allow_free_text"])
        return job

    def create(
        self,
        request: str,
        session_id: str,
        question: AskUserRequest,
        checkpoint: list[dict],
        tool_call_id: str,
        task_dir: str = "",
        target_device: str = "client_browser",
    ) -> dict:
        now = self._now()
        job_id = uuid.uuid4().hex
        expires = now + timedelta(hours=question.timeout_hours)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ask_jobs(
                    id, request, session_id, question, options, allow_free_text, state,
                    question_count, answers, checkpoint, tool_call_id, task_dir, target_device, created, updated, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'waiting_for_user', 1, '[]', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    request,
                    session_id,
                    question.question,
                    json.dumps(question.options),
                    int(question.allow_free_text),
                    json.dumps(checkpoint),
                    tool_call_id,
                    task_dir,
                    target_device,
                    now.isoformat(),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
        return self.get(job_id)

    def create_running(
        self,
        request: str,
        session_id: str,
        task_dir: str = "",
        target_device: str = "client_browser",
    ) -> dict:
        now = self._now().isoformat()
        job_id = uuid.uuid4().hex
        expires = (self._now() + timedelta(hours=24)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ask_jobs(
                    id, request, session_id, question, options, allow_free_text, state,
                    question_count, answers, checkpoint, tool_call_id, task_dir, target_device, created, updated, expires_at
                ) VALUES (?, ?, ?, '', '[]', 1, 'running', 0, '[]', '[]', '', ?, ?, ?, ?, ?)""",
                (job_id, request, session_id, task_dir, target_device, now, now, expires),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        self.expire()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def list(self) -> list[dict]:
        self.expire()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, state, question, options, allow_free_text, question_count,
                expires_at, target_device, device_actions
                FROM ask_jobs ORDER BY created DESC LIMIT 100"""
            ).fetchall()
        return [
            {
                **dict(row),
                "options": json.loads(row["options"]),
                "allow_free_text": bool(row["allow_free_text"]),
                "target_device": row["target_device"],
                "device_actions": json.loads(row["device_actions"]),
                "result": None,
                "error": "",
            }
            for row in rows
        ]

    def expire(self) -> int:
        now = self._now().isoformat()
        with self._connect() as connection:
            return connection.execute(
                """UPDATE ask_jobs SET state = 'cancelled',
                error = 'Question timed out; the job was cancelled.',
                updated = ? WHERE state = 'waiting_for_user' AND expires_at <= ?""",
                (now, now),
            ).rowcount

    def start_expiration_worker(self) -> None:
        if self._expirer is not None and self._expirer.is_alive():
            return

        def expire_periodically() -> None:
            while not self._stop_expirer.wait(60):
                self.expire()

        self._expirer = threading.Thread(target=expire_periodically, daemon=True)
        self._expirer.start()

    def answer(self, job_id: str, answer: str) -> dict:
        self.expire()
        answer = answer.strip()
        if not answer or len(answer) > 4000:
            raise ValueError("The answer must contain 1 to 4000 characters.")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Question job not found.")
            if row["state"] != "waiting_for_user":
                raise ValueError("This question is no longer waiting for an answer.")
            options = json.loads(row["options"])
            if not row["allow_free_text"] and answer not in options:
                raise ValueError("Choose one of the listed options.")
            answers = json.loads(row["answers"])
            answers.append(answer)
            now = self._now().isoformat()
            connection.execute(
                "UPDATE ask_jobs SET state = 'running', answers = ?, updated = ? WHERE id = ?",
                (json.dumps(answers), now, job_id),
            )
            updated = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(updated)

    def pause(self, job_id: str, question: AskUserRequest) -> dict:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT question_count FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Question job not found.")
            if row["question_count"] >= 3:
                connection.execute(
                    "UPDATE ask_jobs SET state = 'cancelled', error = ?, updated = ? WHERE id = ?",
                    ("The job was cancelled because it reached the three-question limit.", now.isoformat(), job_id),
                )
            else:
                connection.execute(
                    """UPDATE ask_jobs SET question = ?, options = ?, allow_free_text = ?,
                    question_count = question_count + 1, state = 'waiting_for_user',
                    tool_call_id = ?, checkpoint = ?, expires_at = ?, updated = ? WHERE id = ?""",
                    (
                        question.question,
                        json.dumps(question.options),
                        int(question.allow_free_text),
                        question.tool_call_id,
                        json.dumps(question.checkpoint),
                        (now + timedelta(hours=question.timeout_hours)).isoformat(),
                        now.isoformat(),
                        job_id,
                    ),
                )
            updated = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(updated)

    def complete(self, job_id: str, result: str, device_actions: list[dict] | None = None) -> dict:
        with self._connect() as connection:
            connection.execute(
                "UPDATE ask_jobs SET state = 'done', result = ?, device_actions = ?, error = '', updated = ? WHERE id = ?",
                (json.dumps(result), json.dumps(device_actions or []), self._now().isoformat(), job_id),
            )
        return self.get(job_id)

    def save_device_actions(self, job_id: str, device_actions: list[dict]) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE ask_jobs SET device_actions = ?, updated = ? WHERE id = ?",
                (json.dumps(device_actions), self._now().isoformat(), job_id),
            )

    def fail(self, job_id: str, error: str) -> dict:
        with self._connect() as connection:
            connection.execute(
                "UPDATE ask_jobs SET state = 'failed', error = ?, updated = ? WHERE id = ?",
                (error[:1000], self._now().isoformat(), job_id),
            )
        return self.get(job_id)
