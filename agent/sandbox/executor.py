from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from collections.abc import Sequence

from config import SCRATCH_DIR

try:
    import resource
except ImportError:
    resource = None

DEFAULT_TIMEOUT_SECONDS = 8
DEFAULT_MEMORY_BYTES = 192 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 60
MAX_MEMORY_BYTES = 512 * 1024 * 1024
MAX_CPU_SECONDS = 30
MAX_OUTPUT_BYTES = 128 * 1024
MAX_SOURCE_BYTES = 512 * 1024
SAFE_ENVIRONMENT_KEYS = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"}
_PROBE_LOCK = threading.Lock()


class SandboxError(RuntimeError):
    """Raised when the requested isolation cannot be provided."""


@dataclass(frozen=True)
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def _validate_limits(timeout_seconds: int, memory_bytes: int) -> None:
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}.")
    if not isinstance(memory_bytes, int) or not 16 * 1024 * 1024 <= memory_bytes <= MAX_MEMORY_BYTES:
        raise ValueError("memory_bytes must be between 16 MiB and 512 MiB.")


def _safe_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in SAFE_ENVIRONMENT_KEYS
    }


@lru_cache(maxsize=1)
def linux_network_sandbox_available() -> bool:
    """Probe whether Linux network namespaces are usable for this process."""
    if sys.platform != "linux" or resource is None:
        return False
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    with _PROBE_LOCK:
        try:
            result = subprocess.run(
                [unshare, "--net", "--", "true"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                env=_safe_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            return False
    return result.returncode == 0


def _limit_resources(memory_bytes: int, cpu_seconds: int) -> None:
    if resource is None:
        raise SandboxError("POSIX resource limits are unavailable on this platform.")
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))


def _output(path: Path) -> str:
    with path.open("rb") as source:
        value = source.read(MAX_OUTPUT_BYTES + 1)
    truncated = len(value) > MAX_OUTPUT_BYTES
    text = value[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return text + ("\n[output truncated]" if truncated else "")


def _run_isolated(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    memory_bytes: int,
) -> SandboxResult:
    if not linux_network_sandbox_available():
        raise SandboxError(
            "Execution refused: Linux resource limits and a usable network namespace are required."
        )
    unshare = shutil.which("unshare")
    if unshare is None:
        raise SandboxError("Execution refused: unshare is not installed.")
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hsx-sandbox-output-", dir=SCRATCH_DIR) as output_dir:
        output_path = Path(output_dir)
        stdout_path = output_path / "stdout"
        stderr_path = output_path / "stderr"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                [unshare, "--net", "--", *command],
                cwd=cwd,
                env=_safe_environment(),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                close_fds=True,
                start_new_session=True,
                preexec_fn=lambda: _limit_resources(
                    memory_bytes,
                    max(1, min(timeout_seconds, MAX_CPU_SECONDS)),
                ),
            )
            timed_out = False
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                returncode = process.wait()
        return SandboxResult(
            returncode=returncode,
            stdout=_output(stdout_path),
            stderr=_output(stderr_path),
            timed_out=timed_out,
        )


def run_command(
    command: Sequence[str],
    *,
    working_directory: Path,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
) -> SandboxResult:
    """Run an argv-only command in a private network namespace with POSIX limits."""
    _validate_limits(timeout_seconds, memory_bytes)
    if (
        not command
        or len(command) > 128
        or any(not isinstance(part, str) or not part for part in command)
        or sum(len(part.encode("utf-8")) for part in command) > 32 * 1024
    ):
        raise ValueError("command must contain non-empty argument strings.")
    cwd = Path(working_directory).resolve()
    if not cwd.is_dir():
        raise ValueError("working_directory must be an existing directory.")
    return _run_isolated(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        memory_bytes=memory_bytes,
    )


def execute_python(
    source: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    memory_bytes: int = DEFAULT_MEMORY_BYTES,
) -> SandboxResult:
    """Execute a source snippet in a private scratch directory; network is denied."""
    _validate_limits(timeout_seconds, memory_bytes)
    if not isinstance(source, str):
        raise TypeError("source must be a string.")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds the 512 KiB sandbox limit.")
    if not linux_network_sandbox_available():
        raise SandboxError(
            "Execution refused: this host cannot provide Linux resource limits and network isolation."
        )
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hsx-sandbox-", dir=SCRATCH_DIR) as directory:
        workdir = Path(directory)
        script = workdir / "main.py"
        script.write_text(source, encoding="utf-8")
        script.chmod(0o600)
        return _run_isolated(
            [sys.executable, "-I", "-B", str(script)],
            cwd=workdir,
            timeout_seconds=timeout_seconds,
            memory_bytes=memory_bytes,
        )
