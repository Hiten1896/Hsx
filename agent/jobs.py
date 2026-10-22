import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


class JobQueue:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, steps TEXT NOT NULL, checkpoint INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                created TEXT NOT NULL, updated TEXT NOT NULL)"""
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def create(self, steps: list[dict]) -> dict:
        if not steps:
            raise ValueError("A job requires at least one step.")
        job_id = uuid.uuid4().hex
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(id, steps, state, created, updated) VALUES (?, ?, 'queued', ?, ?)",
                (job_id, json.dumps(steps), now, now),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["steps"] = json.loads(result["steps"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def list(self) -> list[dict]:
        with self._connect() as connection:
            ids = [row["id"] for row in connection.execute("SELECT id FROM jobs ORDER BY created DESC")]
        return [self.get(job_id) for job_id in ids]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            self._cancelled.add(job_id)
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE jobs SET state = 'cancelled', updated = ? WHERE id = ? AND state NOT IN ('done', 'failed')",
                (self._now(), job_id),
            ).rowcount
        return bool(changed)

    def run(self, job_id: str, executor: Callable[[dict], dict]) -> None:
        job = self.get(job_id)
        if job is None:
            raise KeyError("Job not found.")
        with self._connect() as connection:
            connection.execute("UPDATE jobs SET state = 'running', updated = ? WHERE id = ?", (self._now(), job_id))
        try:
            for index, step in enumerate(job["steps"][job["checkpoint"]:], start=job["checkpoint"]):
                if job_id in self._cancelled:
                    return
                result = executor(step)
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE jobs SET checkpoint = ?, result = ?, updated = ? WHERE id = ?",
                        (index + 1, json.dumps(result), self._now(), job_id),
                    )
            with self._connect() as connection:
                connection.execute("UPDATE jobs SET state = 'done', updated = ? WHERE id = ?", (self._now(), job_id))
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET state = 'failed', error = ?, updated = ? WHERE id = ?",
                    (str(error), self._now(), job_id),
                )

    def run_async(self, job_id: str, executor: Callable[[dict], dict]) -> threading.Thread:
        worker = threading.Thread(target=self.run, args=(job_id, executor), daemon=True)
        worker.start()
        return worker
