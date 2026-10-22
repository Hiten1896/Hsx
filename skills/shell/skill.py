import os
import shlex
import subprocess


def run(command: str) -> dict:
    if os.getenv("HSX_ENABLE_SHELL") != "1":
        return {"success": False, "error": "Shell is disabled. Set HSX_ENABLE_SHELL=1 to enable it."}
    allowed = {item.strip() for item in os.getenv("HSX_SHELL_ALLOWLIST", "").split(",") if item.strip()}
    parts = shlex.split(command, posix=False)
    if not parts or parts[0] not in allowed:
        return {"success": False, "error": "Command is not on the shell allowlist."}
    result = subprocess.run(parts, check=False, capture_output=True, text=True, timeout=30)
    return {"success": result.returncode == 0, "stdout": result.stdout[:65536], "stderr": result.stderr[:65536]}
