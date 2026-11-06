from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


class ApprovalRequest(Exception):
    def __init__(
        self,
        tool: str,
        arguments: dict,
        approval_request: dict,
        checkpoint: list[dict],
        tool_call_id: str,
        timeout_hours: int = 24,
    ):
        self.tool = tool
        self.arguments = arguments
        self.approval_request = approval_request
        self.checkpoint = checkpoint
        self.tool_call_id = tool_call_id
        self.timeout_hours = timeout_hours
        super().__init__(approval_request.get("summary", f"run {tool}"))


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
                sensitive INTEGER NOT NULL DEFAULT 0,
                approval_id TEXT NOT NULL DEFAULT '',
                approval_json TEXT NOT NULL DEFAULT '{}',
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
            if "sensitive" not in columns:
                connection.execute(
                    "ALTER TABLE ask_jobs ADD COLUMN sensitive INTEGER NOT NULL DEFAULT 0"
                )
            if "approval_id" not in columns:
                connection.execute("ALTER TABLE ask_jobs ADD COLUMN approval_id TEXT NOT NULL DEFAULT ''")
            if "approval_json" not in columns:
                connection.execute("ALTER TABLE ask_jobs ADD COLUMN approval_json TEXT NOT NULL DEFAULT '{}'")
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
        for field in ("options", "answers", "checkpoint", "device_actions", "result", "approval_json"):
            job[field] = json.loads(job[field]) if job[field] else ([] if field in {"options", "answers", "checkpoint", "device_actions"} else None)
        job["approval"] = job.pop("approval_json") or {}
        job["allow_free_text"] = bool(job["allow_free_text"])
        job["sensitive"] = bool(job["sensitive"])
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
        sensitive: bool = False,
    ) -> dict:
        now = self._now().isoformat()
        job_id = uuid.uuid4().hex
        expires = (self._now() + timedelta(hours=24)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ask_jobs(
                    id, request, session_id, question, options, allow_free_text, state,
                    question_count, answers, checkpoint, tool_call_id, task_dir, target_device,
                    sensitive, created, updated, expires_at
                ) VALUES (?, ?, ?, '', '[]', 1, 'running', 0, '[]', '[]', '', ?, ?, ?, ?, ?, ?)""",
                (job_id, request, session_id, task_dir, target_device, int(sensitive), now, now, expires),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        self.expire()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def get_pending(self, session_id: str) -> dict | None:
        self.expire()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM ask_jobs WHERE session_id = ?
                AND state IN ('waiting_for_user', 'waiting_for_approval')
                ORDER BY created DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        return self._public(row) if row else None

    def activity(self, session_id: str, limit: int = 8) -> list[dict]:
        self.expire()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, request, state, sensitive, task_dir, created, updated
                FROM ask_jobs WHERE session_id = ?
                ORDER BY created DESC LIMIT ?""",
                (session_id, max(1, min(limit, 20))),
            ).fetchall()
            completed = connection.execute(
                """SELECT created, updated FROM ask_jobs
                WHERE session_id = ? AND state = 'done'
                ORDER BY updated DESC LIMIT 10""",
                (session_id,),
            ).fetchall()
        from agent.ml.dataset_exporter import _redact

        durations = []
        for row in completed:
            try:
                duration = (datetime.fromisoformat(row["updated"]) - datetime.fromisoformat(row["created"])).total_seconds()
            except ValueError:
                continue
            if duration > 0:
                durations.append(duration)
        average_duration = sum(durations) / len(durations) if durations else None
        now = self._now()
        return [
            self._activity_item(
                row,
                _redact(row["request"], 120),
                average_duration,
                now,
            )
            for row in rows
        ]

    @staticmethod
    def _activity_item(row, summary: str, average_duration: float | None, now: datetime) -> dict:
        try:
            created = datetime.fromisoformat(row["created"])
            elapsed = max(0, int((now - created).total_seconds()))
        except ValueError:
            elapsed = 0
        state = row["state"]
        return {
                "id": row["id"],
                "state": state,
                "summary": "Sensitive task" if row["sensitive"] else summary,
                "agent_name": "Hsx",
                "agent_role": "Assistant",
                "task_path": row["task_dir"],
                "elapsed_seconds": elapsed if state in {"running", "queued"} else None,
                "eta_seconds": (
                    max(0, int(average_duration - elapsed))
                    if state in {"running", "queued"} and average_duration is not None
                    else None
                ),
                "created": row["created"],
                "updated": row["updated"],
            }

    def artifact_directories(self, session_id: str) -> list[str]:
        self.expire()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT task_dir FROM ask_jobs
                WHERE session_id = ? AND task_dir != '' ORDER BY task_dir""",
                (session_id,),
            ).fetchall()
        return [row["task_dir"] for row in rows]

    def list(self) -> list[dict]:
        self.expire()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, state, question, options, allow_free_text, question_count,
                expires_at, target_device, device_actions, approval_id, approval_json
                FROM ask_jobs ORDER BY created DESC LIMIT 100"""
            ).fetchall()
        return [
            {
                **dict(row),
                "options": json.loads(row["options"]),
                "allow_free_text": bool(row["allow_free_text"]),
                "target_device": row["target_device"],
                "device_actions": json.loads(row["device_actions"]),
                "approval_id": row["approval_id"],
                "approval": json.loads(row["approval_json"]),
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
                error = CASE WHEN state = 'waiting_for_approval'
                    THEN 'Approval expired; the requested action was not run.'
                    ELSE 'Question timed out; the job was cancelled.' END,
                updated = ? WHERE state IN ('waiting_for_user', 'waiting_for_approval') AND expires_at <= ?""",
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

    def create_approval(
        self,
        request: str,
        session_id: str,
        approval: dict,
        checkpoint: list[dict],
        tool_call_id: str,
        task_dir: str = "",
        target_device: str = "local_host",
        timeout_hours: int = 24,
    ) -> dict:
        now = self._now()
        job_id = uuid.uuid4().hex
        approval_id = uuid.uuid4().hex
        expires = now + timedelta(hours=timeout_hours)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO ask_jobs(
                    id, request, session_id, question, options, allow_free_text, state,
                    question_count, answers, checkpoint, tool_call_id, task_dir, target_device,
                    sensitive, approval_id, approval_json, created, updated, expires_at
                ) VALUES (?, ?, ?, '', '[]', 1, 'waiting_for_approval', 0, '[]', ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    request,
                    session_id,
                    json.dumps(checkpoint),
                    tool_call_id,
                    task_dir,
                    target_device,
                    approval_id,
                    json.dumps(approval),
                    now.isoformat(),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
        return self.get(job_id)

    def pause_approval(self, job_id: str, approval: dict, checkpoint: list[dict], tool_call_id: str) -> dict:
        now = self._now()
        approval_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """UPDATE ask_jobs SET state = 'waiting_for_approval', approval_id = ?,
                approval_json = ?, checkpoint = ?, tool_call_id = ?, error = '',
                expires_at = ?, updated = ? WHERE id = ? AND state = 'running'""",
                (
                    approval_id,
                    json.dumps(approval),
                    json.dumps(checkpoint),
                    tool_call_id,
                    (now + timedelta(hours=24)).isoformat(),
                    now.isoformat(),
                    job_id,
                ),
            )
        return self.get(job_id)

    def answer_approval(self, job_id: str, approval_id: str, answer: str) -> tuple[dict, bool | None]:
        from agent.autonomy import parse_approval_answer

        self.expire()
        answer = answer.strip()
        if not answer or len(answer) > 4000:
            raise ValueError("Answer yes or no to the pending approval.")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Approval job not found.")
            if row["state"] != "waiting_for_approval" or row["approval_id"] != approval_id:
                raise ValueError("This approval is no longer pending.")
            approval = json.loads(row["approval_json"])
            decision = parse_approval_answer(answer)
            now = self._now().isoformat()
            if decision is None:
                reminder = f"I'm waiting for your yes or no on: {approval.get('summary', 'this action')}"
                connection.execute(
                    "UPDATE ask_jobs SET error = ?, updated = ? WHERE id = ?",
                    (reminder, now, job_id),
                )
            else:
                answers = json.loads(row["answers"])
                answers.append(answer)
                connection.execute(
                    "UPDATE ask_jobs SET state = 'running', answers = ?, error = '', updated = ? WHERE id = ?",
                    (json.dumps(answers), now, job_id),
                )
            updated = connection.execute("SELECT * FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(updated), decision

    def pause(self, job_id: str, question: AskUserRequest) -> dict:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT question_count, state FROM ask_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError("Question job not found.")
            if row["state"] == "running":
                if row["question_count"] >= 3:
                    connection.execute(
                        "UPDATE ask_jobs SET state = 'cancelled', error = ?, updated = ? WHERE id = ?",
                        ("The job was cancelled because it reached the three-question limit.", now.isoformat(), job_id),
                    )
                else:
                    connection.execute(
                        """UPDATE ask_jobs SET question = ?, options = ?, allow_free_text = ?,
                        question_count = question_count + 1, state = 'waiting_for_user',
                        tool_call_id = ?, checkpoint = ?, expires_at = ?, updated = ?
                        WHERE id = ? AND state = 'running'""",
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
                """UPDATE ask_jobs SET state = 'done', result = ?, device_actions = ?,
                approval_json = '{}', approval_id = '', error = '', updated = ?
                WHERE id = ? AND state = 'running'""",
                (json.dumps(result), json.dumps(device_actions or []), self._now().isoformat(), job_id),
            )
        return self.get(job_id)

    def cancel_running(self, reason: str = "The job was stopped by the kill switch.") -> list[dict]:
        now = self._now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id FROM ask_jobs WHERE state = 'running'"
            ).fetchall()
            job_ids = [row["id"] for row in rows]
            if job_ids:
                placeholders = ",".join("?" for _ in job_ids)
                connection.execute(
                    f"""UPDATE ask_jobs SET state = 'cancelled', error = ?,
                    approval_json = '{{}}', approval_id = '', updated = ?
                    WHERE state = 'running' AND id IN ({placeholders})""",
                    (reason[:1000], now, *job_ids),
                )
            updated = [
                self._public(row)
                for row in connection.execute(
                    "SELECT * FROM ask_jobs WHERE id IN ("
                    + ",".join("?" for _ in job_ids)
                    + ")",
                    job_ids,
                ).fetchall()
            ] if job_ids else []
        return updated

    def save_device_actions(self, job_id: str, device_actions: list[dict]) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE ask_jobs SET device_actions = ?, updated = ? WHERE id = ?",
                (json.dumps(device_actions), self._now().isoformat(), job_id),
            )

    def fail(self, job_id: str, error: str) -> dict:
        with self._connect() as connection:
            connection.execute(
                """UPDATE ask_jobs SET state = 'failed', approval_json = '{}',
                approval_id = '', error = ?, updated = ? WHERE id = ? AND state = 'running'""",
                (error[:1000], self._now().isoformat(), job_id),
            )
        return self.get(job_id)
