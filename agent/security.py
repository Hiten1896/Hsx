from pathlib import Path


def safe_path(base: Path, user_path: str | Path) -> Path:
    """Resolve a user path inside base, rejecting traversal and symlink escapes."""
    root = base.resolve()
    candidate = (root / Path(user_path)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path must remain inside the allowed directory.")
    if candidate.is_symlink():
        raise ValueError("Symlink paths are not allowed.")
    for parent in candidate.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Symlink paths are not allowed.")
    return candidate
