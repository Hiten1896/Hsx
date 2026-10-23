import base64
import hashlib
import hmac
import secrets
import threading
import time
from pathlib import Path


class ApprovalGuard:
    def __init__(self, hash_path: Path, pin: str | None = None, max_attempts: int = 5, lockout_seconds: int = 300):
        self.hash_path = hash_path
        self.hash_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._attempts = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()
        self.generated_pin = None
        if not self.hash_path.exists():
            if not pin:
                pin = secrets.token_urlsafe(18)
                self.generated_pin = pin
            self.hash_path.write_text(self._hash(pin), encoding="ascii")
            try:
                self.hash_path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def _hash(pin: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, maxmem=64 * 1024 * 1024)
        return "$".join(("scrypt", base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))

    def verify(self, pin: str) -> bool:
        with self._lock:
            now = time.monotonic()
            if now < self._locked_until:
                return False
            stored = self.hash_path.read_text(encoding="ascii").strip().split("$")
            if len(stored) != 3 or stored[0] != "scrypt":
                return False
            salt = base64.b64decode(stored[1])
            expected = base64.b64decode(stored[2])
            supplied = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, maxmem=64 * 1024 * 1024)
            if hmac.compare_digest(supplied, expected):
                self._attempts = 0
                return True
            self._attempts += 1
            if self._attempts >= self.max_attempts:
                self._locked_until = now + self.lockout_seconds
                self._attempts = 0
            return False

    def remaining_lockout(self) -> int:
        return max(0, int(self._locked_until - time.monotonic()))
