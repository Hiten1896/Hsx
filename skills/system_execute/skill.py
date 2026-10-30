import os
import subprocess
from pathlib import Path


def _run_command(command: str, timeout: int = 30, cwd: str | None = None, shell: bool = False) -> dict:
    try:
        completed = subprocess.run(
            command if shell else ["bash", "-lc", command] if os.name != "nt" else command,
            shell=shell,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "success": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[:200000],
            "stderr": completed.stderr[:200000],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "success": False,
            "returncode": None,
            "stdout": (error.stdout or "")[:200000],
            "stderr": (error.stderr or "")[:200000],
            "error": f"Command timed out after {timeout} seconds.",
        }
    except (OSError, ValueError) as error:
        return {"success": False, "returncode": None, "stdout": "", "stderr": "", "error": str(error)}


def _list_directory(path: str, recursive: bool = False, limit: int = 200) -> dict:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {path}")
    entries = []
    if recursive:
        for current, folders, files in os.walk(target):
            current_path = Path(current)
            for name in sorted(folders + files):
                item = current_path / name
                entries.append({
                    "name": name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                })
                if len(entries) >= limit:
                    break
            if len(entries) >= limit:
                break
    else:
        for item in sorted(target.iterdir(), key=lambda child: child.name):
            entries.append({
                "name": item.name,
                "path": str(item),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else None,
            })
            if len(entries) >= limit:
                break
    return {"success": True, "path": str(target), "entries": entries[:limit]}


def _read_file(path: str, encoding: str = "utf-8", limit: int = 200000) -> dict:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise ValueError(f"Path is not a file: {path}")
    data = target.read_text(encoding=encoding, errors="replace")
    payload = data[:limit]
    return {"success": True, "path": str(target), "content": payload, "truncated": len(data) > len(payload)}


def _write_file(path: str, content: str, append: bool = False, encoding: str = "utf-8") -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with target.open(mode, encoding=encoding) as handle:
        handle.write(content)
    return {"success": True, "path": str(target), "bytes_written": len(content.encode(encoding))}


def _service_status(service: str | None = None) -> dict:
    cmd = "systemctl --no-pager list-units --type=service --all" if service is None else f"systemctl --no-pager status {service}"
    result = _run_command(cmd, timeout=20)
    return {
        "success": result["success"],
        "service": service,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
    }


def _resource_usage() -> dict:
    result = _run_command("(ps -eo pid,comm,%cpu,%mem,rss --sort=-%cpu | head -n 20; echo '---'; df -h / /tmp; echo '---'; free -m) 2>&1", timeout=20)
    return {
        "success": result["success"],
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
    }


def _process_list(limit: int = 50) -> dict:
    result = _run_command(f"ps -eo pid,ppid,comm,%cpu,%mem,rss,stat --sort=-%cpu | head -n {limit}", timeout=20)
    return {
        "success": result["success"],
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
    }


def _service_action(service: str, action: str) -> dict:
    valid_actions = {"start", "stop", "restart", "status", "enable", "disable", "reload"}
    if action not in valid_actions:
        raise ValueError(f"Unsupported service action: {action}")
    result = _run_command(f"systemctl {action} {service}", timeout=30)
    return {
        "success": result["success"],
        "service": service,
        "action": action,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("returncode"),
        "error": result.get("error"),
    }


def run(
    action: str,
    command: str = "",
    path: str = "",
    content: str = "",
    timeout: int = 30,
    cwd: str | None = None,
    shell: bool = False,
    append: bool = False,
    encoding: str = "utf-8",
    service: str | None = None,
    service_action: str | None = None,
    limit: int = 50,
    recursive: bool = False,
) -> dict:
    if action == "exec":
        if not command:
            raise ValueError("A command is required for exec actions.")
        return _run_command(command, timeout=timeout, cwd=cwd, shell=shell)
    if action == "read_file":
        if not path:
            raise ValueError("A file path is required for read_file actions.")
        return _read_file(path, encoding=encoding, limit=200000)
    if action == "write_file":
        if not path:
            raise ValueError("A file path is required for write_file actions.")
        return _write_file(path, content or "", append=append, encoding=encoding)
    if action == "list_dir":
        if not path:
            raise ValueError("A directory path is required for list_dir actions.")
        return _list_directory(path, recursive=recursive, limit=limit)
    if action == "stat":
        if not path:
            raise ValueError("A file or directory path is required for stat actions.")
        target = Path(path)
        try:
            stat_result = target.stat()
        except FileNotFoundError as error:
            raise FileNotFoundError(f"Path not found: {path}") from error
        return {
            "success": True,
            "path": str(target),
            "exists": True,
            "is_file": target.is_file(),
            "is_dir": target.is_dir(),
            "size": stat_result.st_size,
            "mtime": stat_result.st_mtime,
        }
    if action == "processes":
        return _process_list(limit=limit)
    if action == "services":
        return _service_status(service)
    if action == "service_action":
        if not service:
            raise ValueError("A service name is required for service_action.")
        return _service_action(service, service_action or "status")
    if action == "resource":
        return _resource_usage()
    raise ValueError(f"Unsupported system_execute action: {action}")
