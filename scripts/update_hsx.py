#!/usr/bin/env python3
"""Apply trusted, fast-forward-only HSX updates from the configured GitHub branch."""

import json
import logging
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path, PurePosixPath
from typing import Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "https://github.com/Hiten1896/Hsx.git"
PENDING_FILE_NAME = "hsx-update-pending.json"
FINGERPRINT_PATTERN = re.compile(r"^[0-9A-F]{40}(?:[0-9A-F]{24})?$")
VALIDSIG_PATTERN = re.compile(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]+)(?: |$)", re.MULTILINE)
SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}: {detail}")
    return result


def _git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", *arguments], cwd=repo, check=check)


def _valid_signer_fingerprint(output: str) -> str | None:
    match = VALIDSIG_PATTERN.search(output.upper())
    return match.group(1) if match else None


def _verify_commit(repo: Path, commit: str, expected_fingerprint: str) -> None:
    result = _git(repo, "verify-commit", "--raw", commit, check=False)
    if result.returncode:
        raise RuntimeError(f"Update commit {commit} has no valid trusted GPG signature.")
    signer = _valid_signer_fingerprint(f"{result.stdout}\n{result.stderr}")
    if signer != expected_fingerprint:
        raise RuntimeError(
            f"Update commit {commit} was signed by {signer or 'an unknown key'}, "
            f"not the configured signer."
        )


def _changed_requirement_files(paths: list[str]) -> list[str]:
    return sorted({
        path
        for path in paths
        if "/" not in path
        and path.startswith("requirements")
        and path.endswith(".txt")
        and PurePosixPath(path).name == path
    })


def _has_node_dependency_changes(paths: list[str]) -> bool:
    return any(path in {"package.json", "package-lock.json"} for path in paths)


def _install_requirements(repo: Path, venv_dir: Path, files: list[str]) -> None:
    if not files:
        return
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise RuntimeError(f"HSX virtual environment Python was not found: {python}")
    for requirement_file in files:
        path = repo / requirement_file
        if path.is_file():
            _run(
                [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(path)],
                cwd=repo,
                timeout=1800,
            )


def _install_node_dependencies(repo: Path) -> None:
    if not shutil.which("npm"):
        raise RuntimeError("Node dependency manifests changed, but npm is not installed.")
    _run(
        ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=repo,
        timeout=1800,
    )


def _pending_file(repo: Path) -> Path:
    git_dir = _git(repo, "rev-parse", "--git-dir").stdout.strip()
    path = Path(git_dir)
    if not path.is_absolute():
        path = repo / path
    return path / PENDING_FILE_NAME


def _reload_marker(repo: Path) -> Path:
    return _pending_file(repo).with_name("hsx-update-reload-required")


def _finish_pending_dependencies(repo: Path, venv_dir: Path) -> bool:
    marker = _pending_file(repo)
    if not marker.exists():
        return False
    try:
        pending = json.loads(marker.read_text(encoding="utf-8"))
        files = pending["requirements"]
        node_dependencies = pending["node_dependencies"]
        if not isinstance(files, list) or not all(
            isinstance(item, str) and item in _changed_requirement_files([item]) for item in files
        ) or not isinstance(node_dependencies, bool):
            raise ValueError("invalid requirement file list")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid pending dependency update marker: {error}") from error
    _install_requirements(repo, venv_dir, files)
    if node_dependencies:
        _install_node_dependencies(repo)
    _reload_marker(repo).write_text("reload\n", encoding="utf-8")
    marker.unlink()
    logging.info("Installed dependencies from pending update %s.", pending.get("commit", "unknown"))
    return True


def _running_service_pid(service_name: str) -> int:
    service = f"{service_name}.service"
    result = _run(
        ["systemctl", "show", "--property=MainPID", "--value", service],
        timeout=15,
    )
    try:
        pid = int(result.stdout.strip())
    except ValueError as error:
        raise RuntimeError(f"Could not determine the main PID for {service}.") from error
    if pid <= 1:
        raise RuntimeError(f"The {service} service is not running; refusing to signal an unknown process.")
    return pid


def _restart_running_process(service_name: str) -> None:
    if not hasattr(signal, "SIGUSR1"):
        raise RuntimeError("The HSX process-reload signal is available only on Linux.")
    os.kill(_running_service_pid(service_name), signal.SIGUSR1)


def update_once(environ: Mapping[str, str] | None = None) -> bool:
    """Apply one configured update; return whether an update/reload was requested."""
    settings = os.environ if environ is None else environ
    if settings.get("HSX_AUTO_UPDATE", "0").strip() != "1":
        logging.info("Automatic updates are disabled.")
        return False

    fingerprint = settings.get("HSX_UPDATE_SIGNER_FINGERPRINT", "").strip().upper()
    if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise RuntimeError(
            "HSX_UPDATE_SIGNER_FINGERPRINT must contain the trusted 40- or 64-character "
            "GPG signing-key fingerprint."
        )

    repo = Path(settings.get("HSX_REPO_DIR", str(REPO_ROOT))).resolve()
    if repo != REPO_ROOT:
        raise RuntimeError(f"Configured repository path does not match this updater: {repo}")
    branch = settings.get("HSX_UPDATE_BRANCH", "main").strip()
    if not branch or branch.startswith("-") or ".." in branch:
        raise RuntimeError(f"Invalid update branch: {branch!r}")
    service_name = settings.get("HSX_SERVICE_NAME", "hsx").strip()
    if not SERVICE_NAME_PATTERN.fullmatch(service_name):
        raise RuntimeError(f"Invalid HSX systemd service name: {service_name!r}")

    top_level = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top_level != repo:
        raise RuntimeError(f"Updater is not running from the configured Git repository: {repo}")
    current_branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if current_branch != branch:
        raise RuntimeError(f"Expected tracking branch {branch!r}, found {current_branch!r}.")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=normal").stdout
    if status.strip():
        raise RuntimeError("Working tree is not clean; refusing to overwrite local changes.")

    remote = settings.get("HSX_UPDATE_REMOTE", DEFAULT_REMOTE).strip()
    configured_remote = _git(repo, "remote", "get-url", "origin").stdout.strip()
    if configured_remote != remote:
        raise RuntimeError(
            f"Git origin does not match HSX_UPDATE_REMOTE ({configured_remote!r}); refusing update."
        )

    venv_dir = Path(settings.get("HSX_VENV_DIR", str(repo / "venv"))).resolve()
    _finish_pending_dependencies(repo, venv_dir)
    reload_marker = _reload_marker(repo)
    reload_required = reload_marker.exists()

    _git(repo, "check-ref-format", "--branch", branch)
    _git(
        repo,
        "fetch",
        "--no-tags",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
    )
    candidate = _git(repo, "rev-parse", f"refs/remotes/origin/{branch}^{{commit}}").stdout.strip()
    current = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if candidate == current:
        if reload_required:
            _restart_running_process(service_name)
            reload_marker.unlink(missing_ok=True)
        else:
            logging.info("HSX is already up to date.")
        return reload_required

    ancestor = _git(repo, "merge-base", "--is-ancestor", "HEAD", candidate, check=False)
    if ancestor.returncode:
        raise RuntimeError("Remote branch is not a fast-forward of local HEAD; refusing update.")

    commits = _git(repo, "rev-list", "--reverse", f"{current}..{candidate}").stdout.splitlines()
    for commit in commits:
        _verify_commit(repo, commit, fingerprint)

    changed_paths = _git(repo, "diff", "--name-only", f"{current}..{candidate}").stdout.splitlines()
    requirements = _changed_requirement_files(changed_paths)
    node_dependencies = _has_node_dependency_changes(changed_paths)
    _git(repo, "merge", "--ff-only", candidate)

    if requirements or node_dependencies:
        marker = _pending_file(repo)
        marker.write_text(
            json.dumps({
                "commit": candidate,
                "requirements": requirements,
                "node_dependencies": node_dependencies,
            }) + "\n",
            encoding="utf-8",
        )
        _finish_pending_dependencies(repo, venv_dir)

    logging.info("Updated HSX from %s to %s.", current[:12], candidate[:12])
    _restart_running_process(service_name)
    reload_marker.unlink(missing_ok=True)
    return True


def main() -> int:
    try:
        update_once()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        logging.error("Automatic update failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
