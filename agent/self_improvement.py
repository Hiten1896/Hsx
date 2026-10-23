import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from agent.approval import ApprovalGuard
from agent.security import safe_path
from config import DATA_DIR, SCRATCH_DIR


class SelfImprovement:
    """Proposal-only self-improvement pipeline; production changes require owner approval."""

    def __init__(self, database_path: Path | None = None, approval_guard: ApprovalGuard | None = None):
        self.database_path = database_path or DATA_DIR / "improvements.db"
        self.approval_guard = approval_guard
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, rationale TEXT NOT NULL,
                status TEXT NOT NULL, sandbox TEXT NOT NULL, checks TEXT NOT NULL DEFAULT '[]',
                created TEXT NOT NULL, updated TEXT NOT NULL)"""
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _get(self, proposal_id: str) -> dict | None:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["checks"] = json.loads(result["checks"])
        return result

    def create(self, title: str, rationale: str) -> dict:
        if not title.strip() or not rationale.strip():
            raise ValueError("Title and rationale are required.")
        proposal_id = uuid.uuid4().hex
        sandbox = SCRATCH_DIR / "self_improvement" / proposal_id
        sandbox.mkdir(parents=True, exist_ok=False)
        now = self._now()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO proposals VALUES (?, ?, ?, 'proposed', ?, '[]', ?, ?)",
                (proposal_id, title.strip(), rationale.strip(), str(sandbox), now, now),
            )
        return self._get(proposal_id)

    def list(self) -> list[dict]:
        with sqlite3.connect(self.database_path) as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM proposals ORDER BY created DESC")]
        return [self._get(proposal_id) for proposal_id in ids]

    def prototype(self, proposal_id: str, relative_file: str, content: str) -> dict:
        proposal = self._get(proposal_id)
        if proposal is None:
            raise KeyError("Proposal not found.")
        target = safe_path(Path(proposal["sandbox"]), relative_file)
        if target.suffix not in {".py", ".json", ".md", ".txt"}:
            raise ValueError("Only reviewable text prototype files are allowed.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE proposals SET status = 'prototyped', updated = ? WHERE id = ?",
                (self._now(), proposal_id),
            )
        return self._get(proposal_id)

    def run_strict_checks(self, proposal_id: str) -> dict:
        proposal = self._get(proposal_id)
        if proposal is None:
            raise KeyError("Proposal not found.")
        sandbox = Path(proposal["sandbox"]).resolve()
        tests = list(sandbox.rglob("test_*.py"))
        if not tests:
            raise ValueError("A proposal must include at least one test_*.py file.")
        safe_environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}
        }
        result = subprocess.run(
            ["python", "-m", "pytest", "-q", *[str(test) for test in tests]],
            cwd=sandbox, capture_output=True, text=True, timeout=120,
            env=safe_environment,
            check=False,
        )
        check = {"passed": result.returncode == 0, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE proposals SET status = ?, checks = ?, updated = ? WHERE id = ?",
                ("awaiting_approval" if check["passed"] else "failed", json.dumps([check]), self._now(), proposal_id),
            )
        return self._get(proposal_id)

    def approve(self, proposal_id: str, pin: str) -> dict:
        proposal = self._get(proposal_id)
        if proposal is None:
            raise KeyError("Proposal not found.")
        if self.approval_guard is None or not self.approval_guard.verify(pin):
            raise PermissionError("A valid owner PIN is required.")
        if proposal["status"] != "awaiting_approval" or not all(check["passed"] for check in proposal["checks"]):
            raise ValueError("Proposal must pass strict checks before approval.")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE proposals SET status = 'approved', updated = ? WHERE id = ?",
                (self._now(), proposal_id),
            )
        return self._get(proposal_id)

    def promote(self, proposal_id: str, relative_file: str, target: Path, pin: str) -> dict:
        proposal = self._get(proposal_id)
        if proposal is None:
            raise KeyError("Proposal not found.")
        if self.approval_guard is None or not self.approval_guard.verify(pin):
            raise PermissionError("A valid owner PIN is required.")
        if proposal["status"] != "approved":
            raise PermissionError("Only owner-approved proposals can be promoted.")
        source = safe_path(Path(proposal["sandbox"]), relative_file)
        destination = safe_path(target.parent, target.name)
        if destination.suffix not in {".py", ".json", ".md"}:
            raise ValueError("Promotion target is not an allowed source/document file.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE proposals SET status = 'implemented', updated = ? WHERE id = ?",
                (self._now(), proposal_id),
            )
        return self._get(proposal_id)
