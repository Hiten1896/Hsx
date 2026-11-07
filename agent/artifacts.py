from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class ArtifactStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    family TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created TEXT NOT NULL,
                    UNIQUE(session_id, path)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS artifacts_session_created ON artifacts(session_id, created)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _family(name: str) -> str:
        return re.sub(r" \(\d+\)(?=\.[^.]+$|$)", "", name).casefold()

    def record(self, session_id: str, path: str) -> dict:
        target = Path(path)
        name = target.name
        file_type = target.suffix.lower().lstrip(".") or "file"
        family = self._family(name)
        created = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT version FROM artifacts WHERE session_id = ? AND path = ?",
                (session_id, path),
            ).fetchone()
            if existing:
                version = int(existing["version"])
                connection.execute(
                    "UPDATE artifacts SET created = ? WHERE session_id = ? AND path = ?",
                    (created, session_id, path),
                )
            else:
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM artifacts "
                    "WHERE session_id = ? AND family = ?",
                    (session_id, family),
                ).fetchone()
                version = int(row["version"]) + 1
                connection.execute(
                    """INSERT INTO artifacts(session_id, path, name, type, family, version, created)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, path, name, file_type, family, version, created),
                )
        return {
            "session_id": session_id,
            "path": path,
            "name": name,
            "type": file_type,
            "version": version,
            "created": created,
        }

    def list(self, session_id: str = "") -> list[dict]:
        with self._connect() as connection:
            if session_id:
                rows = connection.execute(
                    "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created DESC, id DESC",
                    (session_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM artifacts ORDER BY created DESC, id DESC"
                ).fetchall()
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            item = {
                "session_id": row["session_id"],
                "path": row["path"],
                "name": row["name"],
                "type": row["type"],
                "version": int(row["version"]),
                "created": row["created"],
            }
            grouped.setdefault((item["session_id"], row["family"]), []).append(item)
        items = []
        for versions in grouped.values():
            latest = dict(versions[0])
            latest["versions"] = sorted(versions, key=lambda version: version["version"], reverse=True)
            items.append(latest)
        return sorted(items, key=lambda item: item["created"], reverse=True)

    def migrate_from_workspace(
        self,
        workspace,
        session_id: str = "",
        ask_jobs=None,
        allowed_suffixes: set[str] | None = None,
    ) -> list[dict]:
        """One-time cleanup that imports existing deliverables while excluding scratch JSON and scripts."""
        if allowed_suffixes is None:
            allowed_suffixes = {
                ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".md", ".txt", ".html",
                ".htm", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".py",
                ".js", ".jsx", ".ts", ".tsx", ".css", ".json", ".yaml", ".yml",
                ".toml", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".cs", ".sh",
                ".zip",
            }
        work_dir = getattr(workspace, "work_dir", None)
        if not work_dir or not Path(work_dir).is_dir():
            return []

        task_dir_to_session: dict[str, str] = {}
        if ask_jobs is not None:
            try:
                with ask_jobs._connect() as connection:
                    rows = connection.execute(
                        "SELECT session_id, task_dir FROM ask_jobs WHERE task_dir != ''"
                    ).fetchall()
                    for r in rows:
                        task_dir_to_session[r["task_dir"]] = r["session_id"]
            except Exception:
                pass

        registered = []
        root = Path(workspace.root).resolve()
        work_path = Path(work_dir).resolve()
        for candidate in work_path.rglob("*"):
            if not candidate.is_file():
                continue
            name = candidate.name
            suffix = candidate.suffix.lower()
            # Explicitly exclude internal scratch scripts, json inputs/outputs, logs, temp files
            if name == "activity-trace.jsonl" or name.startswith("scratch") or name.startswith("."):
                continue
            if suffix in {".jsonl", ".log", ".tmp", ".temp"}:
                continue
            # For JSON and scripts, only keep if not in internal/scratch patterns
            if suffix in {".json", ".sh"} and (
                "scratch" in name
                or "task" in name
                or "input" in name
                or "output" in name
                or "tool" in name
            ):
                continue
            if suffix not in allowed_suffixes:
                continue
            rel_path = candidate.resolve().relative_to(root).as_posix()

            target_session = session_id
            if not target_session:
                rel_work = candidate.resolve().relative_to(work_path).as_posix()
                top_folder = rel_work.split("/")[0] if "/" in rel_work else ""
                target_session = task_dir_to_session.get(top_folder, "")

            item = self.record(target_session, rel_path)
            registered.append(item)
        return registered
