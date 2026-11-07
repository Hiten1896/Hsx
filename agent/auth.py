from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

MIN_TOKEN_LENGTH = 32


def _validate_token(token: str, source: str) -> str:
    value = token.strip()
    if len(value) < MIN_TOKEN_LENGTH or not value.isascii() or any(char.isspace() for char in value):
        raise ValueError(f"{source} must be an ASCII token of at least {MIN_TOKEN_LENGTH} characters.")
    return value


def _read_existing_token(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"{path} must not be a symbolic link.")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{path} must be a regular file.")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        path.chmod(0o600)
    return _validate_token(path.read_text(encoding="ascii"), str(path))


def load_or_create_auth_token(path: Path, configured: str = "") -> str:
    """Return a configured token or create and persist a local bearer token."""
    if configured.strip():
        return _validate_token(configured, "HSX_AUTH_TOKEN")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = _read_existing_token(path)
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return _read_existing_token(path)
        with os.fdopen(descriptor, "w", encoding="ascii") as token_file:
            token_file.write(f"{token}\n")
            token_file.flush()
            os.fsync(token_file.fileno())
        os.chmod(path, 0o600)
        return token
    return token
