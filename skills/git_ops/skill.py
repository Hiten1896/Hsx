import os
import subprocess
from pathlib import Path


def run(operation: str, message: str = "") -> dict:
    repository = Path(os.getenv("HSX_GIT_REPO", Path.cwd())).resolve()
    if not (repository / ".git").exists():
        return {"success": False, "error": "Configured repository is unavailable."}
    commands = {"status": ["status", "--short"], "diff": ["diff", "--no-ext-diff"]}
    if operation == "commit":
        if not message.strip():
            return {"success": False, "error": "Commit message is required."}
        commands[operation] = ["commit", "-am", message]
    if operation not in commands:
        return {"success": False, "error": "Operation must be status, diff, or commit."}
    result = subprocess.run(["git", *commands[operation]], cwd=repository, check=False, capture_output=True, text=True, timeout=30)
    return {"success": result.returncode == 0, "stdout": result.stdout[:65536], "stderr": result.stderr[:65536]}
