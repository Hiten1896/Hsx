"""Tools that let Hsx work on the HP PC: over SSH from another machine, or directly when it runs there.

They use the system OpenSSH client (already present on Windows 10/11 and Debian), so your existing
keys, ssh-agent, ~/.ssh/config and known_hosts are reused and no new Python dependency is needed.

Modes (HSX_REMOTE_MODE): ssh | local | auto (default).
    ssh    run on another machine over SSH.
    local  run the same commands on this machine (use when the Hsx server itself runs on the HP PC).
    auto   ssh when HSX_SSH_HOST is set, otherwise local on Linux, otherwise disabled.

Environment (.env):
    HSX_SSH_HOST       hostname, Tailscale IP, or a Host alias from ~/.ssh/config
                       (on Windows the existing HSX_REMOTE_NODE_HOST / HSX_REMOTE_NODE_USER are used if this is empty)
    HSX_SSH_USER       optional; leave empty if the alias already sets it
    HSX_SSH_PORT       optional, default 22
    HSX_SSH_KEY        optional path to a private key (key auth only; passwords are never used)
    HSX_REMOTE_ROOT    project root on the remote machine, default ~/hsx-projects
    HSX_REMOTE_SHELL   default "bash -l -s" (login shell so PATH from ~/.profile is available)
    HSX_REMOTE_AUTONOMY  strict | dev (default) | full   (see agent/remote_policy.py)

Approval and blocking are decided by agent/policy.py via agent/remote_policy.py before any tool runs.
Tools return {"success": ...} dictionaries and never raise anything the agent loop does not expect.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import shlex
import subprocess
import threading
import time
from typing import Any

from agent import remote_policy as policy

LOGGER = logging.getLogger("hsx.remote")

MAX_OUTPUT_CHARS = 12000
MAX_FILE_BYTES = 1_000_000
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,40}$")
JOB_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")

_home_cache = ""
_home_lock = threading.Lock()


def _redact(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    try:
        from agent.activity_trace import redact_activity_text

        return redact_activity_text(text, limit)
    except Exception:  # pragma: no cover - redaction is best effort
        return text[:limit]


def _trim(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} characters omitted]...\n{text[-tail:]}"


def _host() -> str:
    """HSX_SSH_HOST, or (on Windows only) the existing HSX_REMOTE_NODE_HOST setting."""
    explicit = os.getenv("HSX_SSH_HOST", "").strip()
    if explicit:
        return explicit
    return os.getenv("HSX_REMOTE_NODE_HOST", "").strip() if os.name == "nt" else ""


def _user() -> str:
    explicit = os.getenv("HSX_SSH_USER", "").strip()
    if explicit or os.getenv("HSX_SSH_HOST", "").strip():
        return explicit
    return os.getenv("HSX_REMOTE_NODE_USER", "").strip() if os.name == "nt" else ""


def mode() -> str:
    """Return "ssh", "local" or "" (disabled)."""
    wanted = os.getenv("HSX_REMOTE_MODE", "auto").strip().lower()
    host = _host()
    if wanted == "ssh":
        return "ssh" if host else ""
    if wanted == "local":
        return "local" if os.name == "posix" else ""
    if host:
        return "ssh"
    return "local" if os.name == "posix" else ""


def configured() -> bool:
    return bool(mode())


def _target() -> str:
    host = _host()
    user = _user()
    if not host:
        raise RuntimeError("Remote access is not configured. Set HSX_SSH_HOST in .env.")
    if not re.fullmatch(r"[A-Za-z0-9_.:@\-\[\]]+", host) or host.startswith("-"):
        raise RuntimeError("HSX_SSH_HOST contains characters that are not allowed.")
    if user and not re.fullmatch(r"[A-Za-z0-9_.\-]+", user):
        raise RuntimeError("HSX_SSH_USER contains characters that are not allowed.")
    return f"{user}@{host}" if user else host


def _ssh_command(remote_command: str) -> list[str]:
    command = [
        os.getenv("HSX_SSH_BIN", "ssh"),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=yes",
    ]
    port = os.getenv("HSX_SSH_PORT", "").strip()
    if port:
        if not port.isdigit():
            raise RuntimeError("HSX_SSH_PORT must be a number.")
        command += ["-p", port]
    key = os.getenv("HSX_SSH_KEY", "").strip()
    if key:
        command += ["-i", key, "-o", "IdentitiesOnly=yes"]
    command += [_target(), remote_command]
    return command


def _explain_ssh_failure(stderr: str) -> str:
    low = stderr.lower()
    if "host key verification failed" in low or "no matching host key" in low:
        return "SSH host key is not trusted yet. Run `ssh <user>@<host>` once in a terminal and accept the key."
    if "permission denied" in low:
        return "SSH key login was rejected. Check HSX_SSH_USER, HSX_SSH_KEY and that the key is in the HP PC's authorized_keys."
    if "timed out" in low or "no route" in low or "unreachable" in low or "could not resolve" in low:
        return "The HP PC is not reachable. Check that it is on, and that Tailscale is connected on both machines."
    if "connection refused" in low:
        return "The HP PC refused the SSH connection. Check that the SSH server is running."
    return stderr.strip().splitlines()[-1][:300] if stderr.strip() else "SSH failed."


def _run(remote_command: str, *, stdin: str | bytes | None = None, timeout: float = 60.0) -> dict[str, Any]:
    """Run one ssh invocation. Returns a result dict; raises only RuntimeError for local setup problems."""
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    started = time.monotonic()
    local = mode() == "local"
    try:
        completed = subprocess.run(
            ["bash", "-c", remote_command] if local else _ssh_command(remote_command),
            input=data,
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "timed_out": True, "error": f"Timed out after {int(timeout)} seconds."}
    except FileNotFoundError as error:
        raise RuntimeError(
            "bash was not found on this machine." if local else
            "The OpenSSH client (ssh) was not found. On Windows enable 'OpenSSH Client' in Optional Features."
        ) from error
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    result: dict[str, Any] = {
        "success": completed.returncode == 0,
        "exit_status": completed.returncode,
        "stdout": _redact(_trim(stdout)),
        "stderr": _redact(_trim(stderr, 4000), 4000),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if completed.returncode == 255 and not local:
        result["error"] = _explain_ssh_failure(stderr)
        result["ssh_failure"] = True
    elif completed.returncode == 124:
        result["error"] = "The command hit its time limit and was stopped."
        result["timed_out"] = True
    elif completed.returncode == 97:
        result["error"] = "The working directory does not exist on the HP PC."
    elif completed.returncode != 0:
        result["error"] = f"The command exited with status {completed.returncode}."
    return result


def remote_home() -> str:
    global _home_cache
    with _home_lock:
        if _home_cache:
            return _home_cache
    result = _run('printf %s "$HOME"', timeout=20)
    if not result.get("success") or not result.get("stdout", "").startswith("/"):
        raise RuntimeError(result.get("error") or "Could not read the remote home directory.")
    with _home_lock:
        _home_cache = result["stdout"].strip()
    policy.set_home(_home_cache)
    return _home_cache


def _abs(path: str, cwd: str | None = None) -> str:
    """Resolve a user-supplied path to an absolute remote path using the policy's normalisation."""
    home = remote_home()
    if "\x00" in path or "\n" in path or len(path) > 500:
        raise ValueError("Path contains invalid characters.")
    sym = policy.resolve_path(path, cwd)
    if sym.startswith("~"):
        return home + sym[1:]
    return sym


def _project_root_abs() -> str:
    return _abs(policy.root(), None)


def _in_project(abs_path: str, min_depth: int = 1) -> bool:
    return policy.project_depth(policy.resolve_path(abs_path, None)) >= min_depth


# ---------------------------------------------------------------- tools

def remote_exec(**arguments: Any) -> dict:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string.")
    try:
        timeout = int(arguments.get("timeout_seconds", 120))
    except (TypeError, ValueError):
        timeout = 120
    timeout = max(1, min(timeout, 900))
    cwd_arg = arguments.get("cwd")
    root_abs = _project_root_abs()
    cwd_abs = _abs(str(cwd_arg), None) if cwd_arg else root_abs
    action = policy.classify_command(command, str(cwd_arg) if cwd_arg else None)
    if action in {"remote_blocked", "read_secrets"}:
        return {"success": False, "error": "This command is blocked by the host safety policy."}
    shell = os.getenv("HSX_REMOTE_SHELL", "bash -l -s").strip() or "bash -l -s"
    script = f"mkdir -p {shlex.quote(root_abs)}\ncd {shlex.quote(cwd_abs)} || exit 97\n{command}\n"
    remote = f"timeout --kill-after=5 {timeout} {shell}"
    result = _run(remote, stdin=script, timeout=timeout + 20)
    result["cwd"] = cwd_abs
    return result


def _parse_kv(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


_SYSINFO_SCRIPT = r"""
KV() { printf '%s=%s\n' "$1" "$2"; }
KV os "$( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -s )"
KV kernel "$(uname -r)"
KV arch "$(uname -m)"
KV cpu "$(lscpu 2>/dev/null | awk -F: '/Model name/{sub(/^ +/,"",$2); print $2; exit}')"
KV cores "$(nproc 2>/dev/null)"
KV ram_total_mb "$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')"
KV ram_avail_mb "$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')"
KV swap_mb "$(free -m 2>/dev/null | awk '/^Swap:/{print $2}')"
KV disk_free_gb "$(df -BG --output=avail "$HOME" 2>/dev/null | tail -1 | tr -dc 0-9)"
KV kvm "$([ -e /dev/kvm ] && echo yes || echo no)"
KV virt_cpu_flags "$(grep -Ec '(vmx|svm)' /proc/cpuinfo 2>/dev/null)"
KV gpu "$(lspci 2>/dev/null | grep -Ei 'vga|3d|display' | head -1 | cut -d: -f3- | sed 's/^ *//')"
KV desktop_session "$([ -n "$DISPLAY$WAYLAND_DISPLAY" ] && echo yes || echo no)"
KV uptime "$(uptime -p 2>/dev/null)"
KV load "$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
for t in git gh java javac python3 node npm docker gradle adb sdkmanager ffmpeg tmux; do
  KV "tool_$t" "$(command -v "$t" 2>/dev/null)"
done
KV java_version "$(java -version 2>&1 | head -1)"
KV node_version "$(node --version 2>/dev/null)"
KV python_version "$(python3 --version 2>/dev/null)"
KV git_version "$(git --version 2>/dev/null)"
KV sudo_nopasswd "$(sudo -n true 2>/dev/null && echo yes || echo no)"
KV home "$HOME"
"""


def _assessment(info: dict[str, str]) -> list[str]:
    notes = []

    def number(key: str) -> float:
        try:
            return float(info.get(key, "") or 0)
        except ValueError:
            return 0.0

    ram_gb = number("ram_total_mb") / 1024
    disk = number("disk_free_gb")
    java_major = 0
    match = re.search(r'"?(\d+)(?:\.\d+)?', info.get("java_version", ""))
    if match:
        java_major = int(match.group(1)) if match.group(1) != "1" else 8
    if ram_gb < 8:
        notes.append(f"Only {ram_gb:.1f} GB RAM: a full IDE and emulator are not advisable; use command-line builds.")
    elif ram_gb < 16:
        notes.append(f"{ram_gb:.1f} GB RAM: command-line builds are fine; an Android emulator will be tight.")
    else:
        notes.append(f"{ram_gb:.1f} GB RAM is comfortable for builds.")
    if disk and disk < 20:
        notes.append(f"Only {disk:.0f} GB free disk: SDKs and build caches may not fit.")
    if info.get("kvm") != "yes" or number("virt_cpu_flags") == 0:
        notes.append("No KVM/virtualisation available: an Android emulator would be unusable; build only or use a physical device.")
    if info.get("desktop_session") != "yes":
        notes.append("No desktop session detected: prefer command-line tools over GUI apps like Android Studio.")
    if not info.get("tool_java"):
        notes.append("Java is not installed (a JDK is needed for Android/Gradle builds).")
    elif java_major and java_major < 17:
        notes.append(f"Java {java_major} is installed; recent Android Gradle plugins need JDK 17 or newer.")
    if info.get("sudo_nopasswd") != "yes":
        notes.append("sudo needs a password, so system package installs must be done by you (or use user-level installs).")
    notes.append("These are heuristics; confirm current requirements in the official docs before large installs.")
    return notes


def remote_sysinfo(**arguments: Any) -> dict:
    result = _run("bash -s", stdin=_SYSINFO_SCRIPT, timeout=60)
    if not result.get("success"):
        return result
    info = _parse_kv(result.get("stdout", ""))
    if info.get("home", "").startswith("/"):
        policy.set_home(info["home"])
        global _home_cache
        with _home_lock:
            _home_cache = info["home"]
    return {"success": True, "info": info, "assessment": _assessment(info)}


def remote_files(**arguments: Any) -> dict:
    action = str(arguments.get("action", "list"))
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string.")
    if policy.is_secret_path(path):
        return {"success": False, "error": "This path is blocked by the host safety policy."}
    abs_path = _abs(path, arguments.get("cwd"))
    quoted = shlex.quote(abs_path)
    if action == "list":
        result = _run(f"ls -la --time-style=long-iso -- {quoted}", timeout=30)
        return result
    if action == "read":
        try:
            limit = max(1, min(int(arguments.get("max_bytes", 200000)), MAX_FILE_BYTES))
        except (TypeError, ValueError):
            limit = 200000
        result = _run(f"head -c {limit} -- {quoted}", timeout=30)
        if result.get("success"):
            return {"success": True, "path": abs_path, "content": _redact(result["stdout"], limit), "truncated": len(result["stdout"]) >= limit}
        return result
    if not _in_project(abs_path, 2 if action == "delete" else 1):
        return {"success": False, "error": f"Writes are limited to project folders under {_project_root_abs()}."}
    if action == "write":
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string.")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("content is larger than 1 MB.")
        parent = posixpath.dirname(abs_path)
        executable = " && chmod +x -- " + quoted if arguments.get("executable") else ""
        overwrite = "" if arguments.get("overwrite", True) else f"[ -e {quoted} ] && exit 98; "
        result = _run(f"{overwrite}mkdir -p -- {shlex.quote(parent)} && cat > {quoted}{executable}", stdin=content, timeout=60)
        if result.get("exit_status") == 98:
            return {"success": False, "error": "The file already exists and overwrite is false."}
        if result.get("success"):
            return {"success": True, "path": abs_path, "bytes": len(content.encode("utf-8"))}
        return result
    if action == "mkdir":
        return _run(f"mkdir -p -- {quoted}", timeout=30)
    if action == "delete":
        return _run(f"rm -rf -- {quoted}", timeout=60)
    raise ValueError("action must be list, read, write, mkdir or delete.")


def _job_paths(name: str) -> tuple[str, str, str]:
    base = "$HOME/.hsx/jobs/" + name
    return base + ".log", base + ".pid", base + ".exit"


def remote_job(**arguments: Any) -> dict:
    action = str(arguments.get("action", "status"))
    name = str(arguments.get("name", ""))
    if action != "list" and not JOB_RE.fullmatch(name):
        raise ValueError("name must be 1-40 characters: lowercase letters, digits, - or _.")
    if action == "list":
        result = _run('mkdir -p "$HOME/.hsx/jobs"; cd "$HOME/.hsx/jobs" && ls -1 *.pid 2>/dev/null | sed "s/\\.pid$//"', timeout=30)
        if result.get("success"):
            return {"success": True, "jobs": [line for line in result["stdout"].splitlines() if line]}
        return result
    log, pid, exit_file = _job_paths(name)
    if action == "start":
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string.")
        if policy.classify_command(command, arguments.get("cwd")) in {"remote_blocked", "read_secrets"}:
            return {"success": False, "error": "This command is blocked by the host safety policy."}
        cwd_abs = _abs(str(arguments["cwd"]), None) if arguments.get("cwd") else _project_root_abs()
        shell = os.getenv("HSX_REMOTE_SHELL", "bash -l -s").split()[0] or "bash"
        script = (
            f'mkdir -p "$HOME/.hsx/jobs"; cd {shlex.quote(cwd_abs)} || exit 97\n'
            f'if [ -f "{pid}" ] && kill -0 "$(cat "{pid}")" 2>/dev/null; then echo already_running; exit 99; fi\n'
            f'rm -f "{exit_file}" "{log}"\n'
            f'nohup setsid bash -c \'{shell} -l -c "$1"; echo $? > "$2"\' _ {shlex.quote(command)} "{exit_file}" '
            f'> "{log}" 2>&1 < /dev/null &\n'
            f'echo $! > "{pid}"\necho started\n'
        )
        result = _run("bash -s", stdin=script, timeout=40)
        if result.get("exit_status") == 99:
            return {"success": False, "error": f"Job '{name}' is already running."}
        if result.get("success"):
            return {"success": True, "job": name, "message": "Started in the background. Use remote_job status to follow it."}
        return result
    if action == "status":
        try:
            lines = max(1, min(int(arguments.get("tail_lines", 40)), 300))
        except (TypeError, ValueError):
            lines = 40
        script = (
            f'if [ ! -f "{pid}" ]; then echo state=unknown; exit 0; fi\n'
            f'if [ -f "{exit_file}" ]; then echo state=finished; echo exit_code=$(cat "{exit_file}"); '
            f'elif kill -0 "$(cat "{pid}")" 2>/dev/null; then echo state=running; '
            f'else echo state=stopped; fi\n'
            f'echo ---LOG---\ntail -n {lines} "{log}" 2>/dev/null\n'
        )
        result = _run("bash -s", stdin=script, timeout=30)
        if not result.get("success"):
            return result
        head, _, tail = result["stdout"].partition("---LOG---")
        info = _parse_kv(head)
        return {"success": True, "job": name, "state": info.get("state", "unknown"),
                "exit_code": info.get("exit_code"), "log_tail": _redact(tail.strip("\n"))}
    if action == "stop":
        script = (
            f'[ -f "{pid}" ] || {{ echo not_found; exit 0; }}\n'
            f'p="$(cat "{pid}")"; g="$(ps -o pgid= -p "$p" 2>/dev/null | tr -d " ")"\n'
            f'if [ -n "$g" ]; then kill -TERM -- -"$g" 2>/dev/null; sleep 2; kill -KILL -- -"$g" 2>/dev/null; fi\n'
            f'echo stopped\n'
        )
        result = _run("bash -s", stdin=script, timeout=30)
        return {"success": result.get("success", False), "job": name, "message": result.get("stdout", "").strip() or result.get("error", "")}
    raise ValueError("action must be start, status, stop or list.")


def remote_project_init(**arguments: Any) -> dict:
    name = str(arguments.get("name", ""))
    if not NAME_RE.fullmatch(name):
        raise ValueError("Project name must be 2-41 characters: lowercase letters, digits, . - or _.")
    description = str(arguments.get("description", ""))[:300].replace("\n", " ")
    project = f"{_project_root_abs()}/{name}"
    notes = (
        f"# {name}\n\n{description}\n\n## Status\n- [ ] Project created by Hsx\n\n## Decisions\n\n## Next steps\n"
    )
    script = (
        f"set -e\nmkdir -p {shlex.quote(project)}\ncd {shlex.quote(project)}\n"
        "[ -d .git ] || git init -q -b main 2>/dev/null || git init -q\n"
        "[ -f HSX_NOTES.md ] || cat > HSX_NOTES.md <<'HSX_EOF'\n" + notes + "HSX_EOF\n"
        "echo created\n"
    )
    result = _run("bash -s", stdin=script, timeout=60)
    if result.get("success"):
        return {"success": True, "path": project, "message": "Project folder, git repository and HSX_NOTES.md are ready."}
    return result


def register_remote_tools(tools: list, skill_map: dict) -> bool:
    """Register the SSH tools when HSX_SSH_HOST is configured."""
    if not configured():
        LOGGER.info("Remote tools are disabled (set HSX_SSH_HOST, or run the server on Linux for local mode).")
        return False
    LOGGER.info("Remote tools enabled in %s mode.", mode())
    specs = [
        ("remote_sysinfo", remote_sysinfo,
         "Inspect the HP PC: OS, CPU, RAM, disk, KVM/GPU, installed dev tools, and a plain-language assessment of what it can run (e.g. Android builds). Read-only.",
         {"type": "object", "properties": {}, "required": []}),
        ("remote_exec", remote_exec,
         "Run a shell command on the HP PC. Always pass cwd (a project folder under ~/hsx-projects). "
         "Read-only inspection and normal dev work inside project folders (git add/commit, tests, builds, pip/npm install) run automatically; "
         "sudo, pushes, deletions outside a project and other risky commands ask the owner. Secrets and destructive commands are blocked.",
         {"type": "object", "properties": {
             "command": {"type": "string"},
             "cwd": {"type": "string", "description": "Project folder, e.g. ~/hsx-projects/myapp"},
             "timeout_seconds": {"type": "integer"}}, "required": ["command"]}),
        ("remote_files", remote_files,
         "Read, list, write, mkdir or delete files on the HP PC. Writes and deletes are limited to project folders under ~/hsx-projects. Use this instead of echo/cat redirection to create code files.",
         {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["list", "read", "write", "mkdir", "delete"]},
             "path": {"type": "string"},
             "content": {"type": "string"},
             "executable": {"type": "boolean"},
             "overwrite": {"type": "boolean"}}, "required": ["action", "path"]}),
        ("remote_job", remote_job,
         "Run long commands (downloads, builds, servers) in the background on the HP PC and follow them. action start needs command and cwd; status returns state and the last log lines.",
         {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["start", "status", "stop", "list"]},
             "name": {"type": "string"},
             "command": {"type": "string"},
             "cwd": {"type": "string"},
             "tail_lines": {"type": "integer"}}, "required": ["action"]}),
        ("remote_project_init", remote_project_init,
         "Create a new project folder under ~/hsx-projects on the HP PC with git and an HSX_NOTES.md progress file.",
         {"type": "object", "properties": {
             "name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name"]}),
    ]
    for name, runner, description, schema in specs:
        if name in skill_map or any(item.get("name") == name for item in tools):
            continue
        tools.append({"name": name, "description": description, "input_schema": schema})
        skill_map[name] = runner
    return True
