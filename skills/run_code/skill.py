import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent.sandbox.executor import SandboxError, execute_python
from config import SANDBOX

TIMEOUT_SECONDS = 10
MAX_OUTPUT_BYTES = 64 * 1024


def _trim(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return value
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore") + "\n[output truncated]"


def _safe_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        result = subprocess.run(
            [docker, "info"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _run_docker(code: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    script = workdir / "script.py"
    script.write_text(code, encoding="utf-8")
    workdir.chmod(0o755)
    script.chmod(0o644)
    command = [
        "docker", "run", "--rm", "--network", "none",
        "--memory", "256m", "--cpus", "1", "--pids-limit", "64",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", "65534:65534", "--mount",
        f"type=bind,src={workdir.resolve()},dst=/work,readonly",
        "python:3.11-slim", "python", "/work/script.py",
    ]
    return subprocess.run(
        command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        cwd=workdir, env=_safe_environment(),
    )


def _run_subprocess(code: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    del workdir
    result = execute_python(code, timeout_seconds=TIMEOUT_SECONDS)
    if result.timed_out:
        raise subprocess.TimeoutExpired("sandboxed python", TIMEOUT_SECONDS, output=result.stdout, stderr=result.stderr)
    return subprocess.CompletedProcess(
        args=["sandboxed python"],
        returncode=result.returncode or 0,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def run(code: str) -> dict:
    """Run Python in Docker or a fail-closed Linux network namespace."""
    if not isinstance(code, str):
        return {"success": False, "error": "Code must be a string."}
    try:
        with tempfile.TemporaryDirectory(prefix="hsx-run-") as directory:
            workdir = Path(directory)
            use_docker = SANDBOX == "docker" or (SANDBOX == "auto" and _docker_available())
            if SANDBOX == "docker" and not _docker_available():
                return {"success": False, "error": "Docker sandbox is requested but Docker is unavailable."}
            result = _run_docker(code, workdir) if use_docker else _run_subprocess(code, workdir)
            return {
                "success": result.returncode == 0,
                "stdout": _trim(result.stdout),
                "stderr": _trim(result.stderr),
                "returncode": result.returncode,
                "sandbox": "docker" if use_docker else "subprocess",
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Code execution timed out after {TIMEOUT_SECONDS} seconds."}
    except SandboxError as error:
        return {"success": False, "error": str(error)}
    except OSError as error:
        return {"success": False, "error": f"Code execution failed: {error}"}
