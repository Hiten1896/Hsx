import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.approval import ApprovalGuard
from config import BASE_DIR, DATA_DIR

ALLOWED_DEVICE_TYPES = frozenset({"windows_pc", "android", "telegram", "local_network"})
ALLOWED_CAPABILITIES = frozenset({
    "status.read",
    "notify.send",
    "file.share",
    "camera.capture",
    "location.read",
    "media.control",
    "system.execute",
    "system.files",
    "system.process",
    "system.services",
    "system.resource",
})


class DeviceRegistry:
    """Capability-scoped device pairing and authorization; it executes no device actions."""

    def __init__(
        self,
        database_path: Path | None = None,
        approval_guard: ApprovalGuard | None = None,
        audit_path: Path | None = None,
    ):
        self.database_path = database_path or DATA_DIR / "devices.db"
        self.approval_guard = approval_guard
        self.audit_path = audit_path or (BASE_DIR / "logs" / "device_audit.jsonl")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, device_type TEXT NOT NULL,
                capabilities TEXT NOT NULL, token_hash TEXT NOT NULL,
                created TEXT NOT NULL, last_seen TEXT, revoked INTEGER NOT NULL DEFAULT 0)"""
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _audit(self, event: str, device_id: str, capability: str = "", allowed: bool = False) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": self._now(),
            "event": event,
            "device_id": device_id,
            "capability": capability,
            "allowed": allowed,
        }
        with self.audit_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, sort_keys=True) + "\n")

    def pair(self, device_id: str, name: str, device_type: str, capabilities: list[str], pin: str) -> dict:
        device_id = device_id.strip()
        name = name.strip()
        device_type = device_type.strip().lower()
        requested = frozenset(item.strip() for item in capabilities if item.strip())
        if not device_id or not name:
            raise ValueError("Device id and name are required.")
        if device_type not in ALLOWED_DEVICE_TYPES:
            raise ValueError("Unsupported device type.")
        if not requested or not requested.issubset(ALLOWED_CAPABILITIES):
            raise ValueError("One or more device capabilities are not allowed.")
        if self.approval_guard is None or not self.approval_guard.verify(pin):
            self._audit("pair_denied", device_id)
            raise PermissionError("A valid owner PIN is required.")
        token = secrets.token_urlsafe(32)
        now = self._now()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO devices
                (id, name, device_type, capabilities, token_hash, created, last_seen, revoked)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                device_type=excluded.device_type, capabilities=excluded.capabilities,
                token_hash=excluded.token_hash, created=excluded.created,
                last_seen=NULL, revoked=0""",
                (device_id, name, device_type, json.dumps(sorted(requested)), self._token_hash(token), now),
            )
        self._audit("paired", device_id, allowed=True)
        return {
            "id": device_id,
            "name": name,
            "device_type": device_type,
            "capabilities": sorted(requested),
            "token": token,
            "token_warning": "Store this token securely; it will not be shown again.",
        }

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT id, name, device_type, capabilities, created, last_seen, revoked "
                "FROM devices ORDER BY created DESC"
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "device_type": row[2],
                "capabilities": json.loads(row[3]),
                "created": row[4],
                "last_seen": row[5],
                "revoked": bool(row[6]),
            }
            for row in rows
        ]

    def authorize(self, device_id: str, token: str, capability: str) -> dict:
        with sqlite3.connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT capabilities, token_hash, revoked FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
        if row is None:
            self._audit("authorize_denied", device_id, capability)
            raise PermissionError("Device is not paired.")
        capabilities, token_hash, revoked = row
        valid_token = hmac.compare_digest(self._token_hash(token), token_hash)
        allowed = valid_token and not revoked and capability in json.loads(capabilities)
        self._audit("authorize", device_id, capability, allowed)
        if not allowed:
            raise PermissionError("Device token, status, or capability is invalid.")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (self._now(), device_id))
        return {"device_id": device_id, "capability": capability, "authorized": True}

    def revoke(self, device_id: str, pin: str) -> None:
        if self.approval_guard is None or not self.approval_guard.verify(pin):
            raise PermissionError("A valid owner PIN is required.")
        with sqlite3.connect(self.database_path) as connection:
            changed = connection.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,)).rowcount
        if not changed:
            raise KeyError("Device not found.")
        self._audit("revoked", device_id, allowed=True)
