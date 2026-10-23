import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from config import SANDBOX

try:
    import resource
except ImportError:
    resource = None

TIMEOUT_SECONDS = 10
MAX_OUTPUT_BYTES = 64 * 1024


def _trim(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return value
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="ignore") + "\n[output truncated]"


def _safe_environment() -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run_docker(code: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    script = workdir / "script.py"
    script.write_text(code, encoding="utf-8")
    command = [
        "docker", "run", "--rm", "--network", "none",
        "--memory", "256m", "--cpus", "1", "--pids-limit", "64",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--user", "65534:65534", "--mount",
        f"type=bind,src={workdir.resolve()},dst=/work,readonly",
        "python:3.11-slim", "python", "/work/script.py",
    ]
    return subprocess.run(
        command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        cwd=workdir, env=_safe_environment(),
    )


def _run_subprocess(code: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    script = workdir / "script.py"
    guarded_code = (
        "import socket\n"
        "class _NoNetwork:\n"
        "    def __init__(self, *args, **kwargs): raise OSError('network disabled in subprocess fallback')\n"
        "socket.socket = _NoNetwork\n"
        "socket.create_connection = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('network disabled in subprocess fallback'))\n"
        + code
    )
    script.write_text(guarded_code, encoding="utf-8")
    preexec_fn = None
    if resource is not None:
        def limit_resources() -> None:
            resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS))
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
        preexec_fn = limit_resources
    return subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True,
        timeout=TIMEOUT_SECONDS, cwd=workdir, env=_safe_environment(),
        preexec_fn=preexec_fn,
    )


def run(code: str) -> dict:
    """Run Python in Docker when available, otherwise in a constrained temp dir."""
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
    except OSError as error:
        return {"success": False, "error": f"Code execution failed: {error}"}
