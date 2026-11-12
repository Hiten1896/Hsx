"""Safety classification for Hsx's SSH tools (remote_exec, remote_files, remote_job, ...).

Every remote call is mapped to one of these policy actions:

    remote_read      inspect only                       -> runs automatically
    remote_dev       dev work inside a project folder   -> runs automatically (HSX_REMOTE_AUTONOMY=dev or full)
    remote_mutation  anything else that changes things  -> needs owner approval
    sudo / shutdown_reboot / system_services            -> needs owner approval (existing policy actions)
    read_secrets / remote_blocked                       -> always denied

HSX_REMOTE_AUTONOMY: strict = only reads run automatically, dev = default, full = also skip approval
for ordinary mutations (blocked and secret actions are still denied).
"""

from __future__ import annotations

import os
import posixpath
import re
import shlex

RANK = {
    "remote_read": 0,
    "remote_tool_checked": 0,
    "remote_dev": 1,
    "remote_mutation": 2,
    "system_services": 3,
    "sudo": 3,
    "shutdown_reboot": 3,
    "read_secrets": 4,
    "remote_blocked": 5,
}

_HOME = ""


def set_home(home: str) -> None:
    """Remember the remote $HOME so absolute paths can be compared with the project root."""
    global _HOME
    _HOME = (home or "").strip().rstrip("/")


def _home() -> str:
    return _HOME or os.getenv("HSX_REMOTE_HOME", "").strip().rstrip("/")


def autonomy_mode() -> str:
    mode = os.getenv("HSX_REMOTE_AUTONOMY", "dev").strip().lower()
    return mode if mode in {"strict", "dev", "full"} else "dev"


def _symbolic(path: str) -> str:
    p = (path or "").strip()
    if p.startswith("$HOME") or p.startswith("${HOME}"):
        p = "~" + p.split("HOME", 1)[1].lstrip("}")
    home = _home()
    if home and (p == home or p.startswith(home + "/")):
        p = "~" + p[len(home):]
    return p


def resolve_path(path: str, cwd: str | None = None) -> str:
    """Normalise a remote path to '~/...' (under home) or '/...' without touching the network."""
    p = _symbolic(path)
    if not p.startswith("~") and not p.startswith("/"):
        base = resolve_path(cwd, None) if cwd else root()
        p = base.rstrip("/") + "/" + p
    if p.startswith("~"):
        tail = posixpath.normpath("/" + p[1:].lstrip("/"))
        return "~" + ("" if tail == "/" else tail)
    return posixpath.normpath(p)


def root() -> str:
    raw = os.getenv("HSX_REMOTE_ROOT", "~/hsx-projects").strip() or "~/hsx-projects"
    p = _symbolic(raw)
    if p.startswith("~"):
        tail = posixpath.normpath("/" + p[1:].lstrip("/"))
        return "~" + ("" if tail == "/" else tail)
    return posixpath.normpath(p)


def project_depth(sym_path: str) -> int:
    """0 = the root itself, 1 = a project folder, 2+ = inside a project, -1 = outside the root."""
    base = root()
    if sym_path == base:
        return 0
    if sym_path.startswith(base + "/"):
        return len([part for part in sym_path[len(base) + 1:].split("/") if part])
    return -1


_SECRET_RE = re.compile(
    r"(^|/)("
    r"\.ssh|\.gnupg|\.aws|\.azure|\.kube|\.netrc|\.git-credentials|\.npmrc|\.pypirc|\.docker/config\.json|"
    r"id_(rsa|dsa|ecdsa|ed25519)(?!\.pub)|"
    r"\.env(\.(?!example|sample|template)[\w.-]+)?|"
    r"shadow|gshadow|sudoers|\.config/gh/hosts\.yml|\.bash_history|\.zsh_history"
    r")(/|$|\s)"
)


def is_secret_path(path: str) -> bool:
    return bool(_SECRET_RE.search(_symbolic(path or "")))


_BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+(-\S*\s+)*-\S*[rRfF]\S*\s+(-\S+\s+)*(/|~|\$HOME|/\*|~/\*|\.\./?)(\s|$)"),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\b[^\n;|&]*\bof=/dev/"),
    re.compile(r":\(\)\s*\{"),
    re.compile(r">\s*/dev/(sd|nvme|vd|mmcblk)"),
    re.compile(r"\b(wipefs|fdisk|sfdisk|parted|shred)\b"),
    re.compile(r"\bchmod\s+-R\s+\S+\s+/(\s|$)"),
    re.compile(r"\bchown\s+-R\s+\S+\s+/(\s|$)"),
    re.compile(r"(curl|wget|fetch)\b[^\n]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"),
    re.compile(r"(curl|wget|fetch)\b[^\n]*\|\s*(sudo\s+)?(python\d?|perl|ruby|node)\b"),
    re.compile(r"\b(ba|z)?sh\s+<\(\s*(curl|wget)"),
    re.compile(r"\bhistory\s+-c\b"),
]

_READ_CMDS = {
    "ls", "cat", "head", "tail", "wc", "df", "du", "free", "uname", "uptime", "whoami", "id", "pwd",
    "echo", "printf", "date", "hostname", "lscpu", "lsblk", "lsusb", "lspci", "lsmod", "nproc", "ss",
    "ps", "which", "type", "file", "stat", "grep", "egrep", "fgrep", "rg", "sort", "uniq", "cut", "tr",
    "tree", "realpath", "basename", "dirname", "readlink", "true", "false", "test", "[", "sleep", "cd",
    "diff", "cmp", "md5sum", "sha256sum", "sha1sum", "xxd", "od", "strings", "jq", "lsof", "journalctl",
    "dmesg", "sensors", "groups", "locale", "getconf", "arch", "pgrep", "awk", "less", "more", "column",
    "nl", "tac", "rev", "fold", "expand", "seq", "yes", "whereis", "man", "info",
}
_DEV_TOOLS = {
    "python", "python3", "node", "java", "javac", "gradle", "gradlew", "./gradlew", "mvn", "make", "cmake",
    "cargo", "rustc", "go", "pytest", "ruff", "black", "mypy", "flake8", "tsc", "dotnet", "flutter", "dart",
    "sdkmanager", "avdmanager", "adb", "php", "composer", "bundle", "ruby", "bash", "sh",
}
_FILE_OPS = {"mkdir", "touch", "cp", "mv", "rm", "ln", "chmod", "tee", "rmdir", "install", "sed"}
_ARCHIVE = {"tar", "unzip", "zip", "gzip", "gunzip", "bzip2", "xz"}
_NETFETCH = {"curl", "wget"}
_GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "blame", "describe", "shortlog",
             "remote", "branch", "tag", "stash", "config", "fetch", "grep", "reflog", "cat-file"}
_GIT_DEV = {"init", "add", "commit", "checkout", "switch", "restore", "clone", "pull", "merge", "mv", "rm",
            "cherry-pick", "apply", "am", "worktree", "submodule", "stash", "tag", "branch", "remote", "config"}
_GIT_MUTATING = {"push", "reset", "clean", "rebase", "filter-branch", "update-ref", "gc", "prune", "replace"}
_GH_READ = {("auth", "status"), ("repo", "view"), ("repo", "list"), ("pr", "list"), ("pr", "view"), ("pr", "status"),
            ("pr", "diff"), ("pr", "checks"), ("issue", "list"), ("issue", "view"), ("issue", "status"),
            ("run", "list"), ("run", "view"), ("workflow", "list"), ("workflow", "view"), ("release", "list"),
            ("release", "view"), ("search", "repos"), ("search", "issues"), ("search", "prs"), ("search", "code")}
_URL_RE = re.compile(r"^([a-z][a-z0-9+.-]*://|git@|[\w.-]+@[\w.-]+:)")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_OPERATORS = {"&&", "||", ";", "|", "&", "|&", ";;"}


def _tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _paths_in(words: list[str]) -> list[str]:
    found = []
    for word in words:
        if _URL_RE.match(word):
            continue
        if word.startswith("-"):
            if "=" in word:
                value = word.split("=", 1)[1]
                if "/" in value or value.startswith(("~", ".")):
                    found.append(value)
            continue
        if "/" in word or word.startswith(("~", ".")) or word == "..":
            found.append(word)
    return found


def _all_inside(paths: list[str], cwd: str, min_depth: int = 1) -> bool:
    for path in paths:
        if path in {"/dev/null", "/dev/stdout", "/dev/stderr"}:
            continue
        if project_depth(resolve_path(path, cwd)) < min_depth:
            return False
    return True


def _strip_wrappers(words: list[str]) -> list[str]:
    words = list(words)
    while words:
        head = words[0]
        if _ENV_ASSIGN.match(head):
            words.pop(0)
        elif head in {"env", "time", "nice", "nohup", "command", "exec"}:
            words.pop(0)
        elif head == "timeout" and len(words) > 2:
            del words[:2]
        else:
            break
    return words


def _dev_or_mutation(words: list[str], cwd: str) -> str:
    in_project = project_depth(resolve_path(cwd or "", None)) >= 1 if cwd else False
    if in_project and _all_inside(_paths_in(words[1:]), cwd):
        return "remote_dev"
    return "remote_mutation"


def _classify_segment(words: list[str], cwd: str) -> str:
    words = _strip_wrappers(words)
    if not words:
        return "remote_read"
    cmd = posixpath.basename(words[0]) if not words[0].startswith("./") else words[0]
    args = words[1:]
    plain = [a for a in args if not a.startswith("-")]

    if cmd == "sudo" or cmd == "su" or cmd == "doas":
        return "sudo"
    if cmd in {"shutdown", "reboot", "poweroff", "halt", "init", "telinit"}:
        return "shutdown_reboot"
    if cmd in {"printenv", "env"} and not args:
        return "read_secrets"
    for arg in args:
        if is_secret_path(arg):
            return "read_secrets"
    if cmd in _READ_CMDS:
        if cmd == "less" or cmd == "more":
            return "remote_read"
        return "remote_read"
    if cmd == "find":
        return "remote_mutation" if any(a in {"-exec", "-execdir", "-ok", "-delete", "-fprint", "-fls"} for a in args) else "remote_read"
    if cmd == "ip":
        return "remote_mutation" if any(a in {"set", "add", "del", "delete", "flush", "replace"} for a in args) else "remote_read"
    if cmd == "xargs":
        return "remote_mutation"
    if cmd in {"--version", "-v"}:
        return "remote_read"
    if args and all(a in {"--version", "-version", "-V", "version", "--help", "-h"} for a in args):
        return "remote_read"
    if cmd == "systemctl":
        sub = plain[0] if plain else ""
        return "remote_read" if sub in {"status", "is-active", "is-enabled", "list-units", "list-unit-files", "show", "cat"} else "system_services"
    if cmd in {"service", "update-rc.d", "chkconfig"}:
        return "system_services"
    if cmd in {"apt", "apt-get", "dpkg", "snap", "flatpak", "dnf", "yum", "pacman", "zypper"}:
        sub = plain[0] if plain else ""
        if sub in {"list", "search", "show", "policy", "info", "-l", "-s", "version"} or args[:1] in (["-l"], ["-s"], ["--list"]):
            return "remote_read"
        return "sudo"
    if cmd == "apt-cache":
        return "remote_read"
    if cmd == "docker" or cmd == "docker-compose" or cmd == "podman":
        sub = plain[0] if plain else ""
        if sub == "compose" and len(plain) > 1:
            sub = plain[1]
        return "remote_read" if sub in {"ps", "images", "logs", "inspect", "version", "info", "stats", "top", "ls", "config"} else "remote_mutation"
    if cmd == "git":
        sub = plain[0] if plain else ""
        if sub in _GIT_MUTATING:
            return "remote_mutation"
        if any(a in {"-D", "--hard", "-f", "--force", "--force-with-lease"} for a in args):
            return "remote_mutation"
        if sub == "branch" and any(a in {"-d", "-m", "-M", "-c", "-C"} for a in args):
            return "remote_mutation"
        if sub in {"remote"}:
            return "remote_read" if (len(plain) == 1 or plain[1:2] in (["show"], ["get-url"])) else _dev_or_mutation(words, cwd)
        if sub in {"branch", "tag", "stash", "config"}:
            listing = len(plain) == 1 or any(a in {"-l", "--list", "--get", "--get-all", "list", "-a", "-r", "-v", "-vv"} for a in args)
            if sub == "config" and any(k in " ".join(args).lower() for k in ("token", "password", "credential")):
                return "read_secrets"
            return "remote_read" if listing else _dev_or_mutation(words, cwd)
        if sub in _GIT_READ - {"remote", "branch", "tag", "stash", "config"}:
            return "remote_read"
        if sub in _GIT_DEV:
            return _dev_or_mutation(words, cwd)
        return "remote_mutation"
    if cmd == "gh":
        key = tuple(plain[:2])
        if key in _GH_READ or (plain[:1] == ["auth"] and plain[1:2] == ["status"]):
            return "remote_read"
        if key == ("repo", "clone"):
            return _dev_or_mutation(words, cwd)
        return "remote_mutation"
    if cmd in {"pip", "pip3", "uv", "poetry"} or (cmd in {"python", "python3"} and args[:2] == ["-m", "pip"]):
        rest = plain[1:] if cmd in {"python", "python3"} else plain
        sub = rest[0] if rest else ""
        if sub in {"list", "show", "freeze", "check", "search", "config", "--version", "inspect"}:
            return "remote_read"
        if sub in {"uninstall", "cache", "publish"} or any(a in {"--user", "--break-system-packages", "--target", "--prefix"} for a in args):
            return "remote_mutation"
        return _dev_or_mutation(words, cwd)
    if cmd in {"npm", "npx", "yarn", "pnpm", "bun"}:
        sub = plain[0] if plain else ""
        if sub in {"ls", "list", "view", "info", "outdated", "audit", "why", "explain", "pack", "version", "root", "prefix"} and not any(a in {"fix"} for a in plain):
            return "remote_read"
        if sub in {"publish", "login", "adduser", "logout", "unpublish", "deprecate", "access", "token", "owner", "team", "config", "set", "link"} or any(a in {"-g", "--global"} for a in args):
            return "remote_mutation"
        return _dev_or_mutation(words, cwd)
    if cmd in _DEV_TOOLS:
        if cmd in {"bash", "sh"} and (not plain or "-c" in args):
            return "remote_mutation"
        return _dev_or_mutation(words, cwd)
    if cmd in _FILE_OPS:
        if cmd == "sed" and "-i" not in " ".join(args) and not any(a.startswith("-i") for a in args):
            return "remote_read"
        candidates = list(plain)
        if cmd == "chmod" and candidates:
            candidates = candidates[1:]
        if not candidates:
            return "remote_mutation"
        if cmd in {"rm", "rmdir"}:
            ok = all(project_depth(resolve_path(p, cwd)) >= 2 for p in candidates)
        else:
            ok = all(project_depth(resolve_path(p, cwd)) >= 1 for p in candidates)
        return "remote_dev" if ok and cwd and project_depth(resolve_path(cwd, None)) >= 1 else "remote_mutation"
    if cmd in _ARCHIVE or cmd in _NETFETCH:
        return _dev_or_mutation(words, cwd)
    return "remote_mutation"


def classify_command(command: str, cwd: str | None = None) -> str:
    """Return the most severe action across every part of a (possibly chained) command."""
    if not isinstance(command, str) or not command.strip():
        return "remote_read"
    text = command.strip()
    if len(text) > 4000:
        return "remote_mutation"
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(text):
            return "remote_blocked"
    if "<<" in text or "`" in text or "$(" in text or "<(" in text or ">(" in text:
        worst = "remote_mutation"
    else:
        worst = "remote_read"
    cwd_sym = cwd or ""
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            tokens = _tokens(line)
        except ValueError:
            return "remote_mutation"
        segment: list[str] = []
        segments: list[list[str]] = []
        redirect_target_next = False
        for token in tokens + [";"]:
            if token in _OPERATORS:
                if segment:
                    segments.append(segment)
                segment = []
                continue
            if token and token[0] in "<>":
                redirect_target_next = token in {">", ">>", ">|"}
                continue
            if redirect_target_next:
                redirect_target_next = False
                safe = token == "/dev/null" or bool(cwd_sym and _all_inside([token], cwd_sym))
                level = "remote_dev" if safe else "remote_mutation"
                if RANK[level] > RANK[worst]:
                    worst = level
                continue
            segment.append(token)
        for words in segments:
            action = _classify_segment(words, cwd_sym)
            if RANK[action] > RANK[worst]:
                worst = action
    return worst


def _apply_mode(action: str) -> str:
    mode = autonomy_mode()
    if action == "remote_dev" and mode == "strict":
        return "remote_mutation"
    if action == "remote_mutation" and mode == "full":
        return "remote_dev"
    return action


def classify_remote_action(name: str, arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "remote_sysinfo":
        return "remote_read"
    if name == "remote_project_init":
        return _apply_mode("remote_dev")
    if name in {"remote_exec"}:
        return _apply_mode(classify_command(str(args.get("command", "")), args.get("cwd")))
    if name == "remote_job":
        action = str(args.get("action", "status"))
        if action in {"status", "list"}:
            return "remote_read"
        if action == "stop":
            return _apply_mode("remote_dev")
        return _apply_mode(classify_command(str(args.get("command", "")), args.get("cwd")))
    if name == "remote_files":
        action = str(args.get("action", "list"))
        path = str(args.get("path", ""))
        if is_secret_path(path):
            return "read_secrets"
        if action in {"read", "list"}:
            return "remote_read"
        depth = project_depth(resolve_path(path, None))
        needed = 2 if action == "delete" else 1
        if depth < needed:
            # The tool itself refuses writes outside project folders and explains why, so do not
            # ask the owner to approve something that cannot happen.
            return "remote_tool_checked"
        return _apply_mode("remote_dev")
    return "remote_mutation"


def describe_remote_action(name: str, arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "remote_exec":
        return f"run `{str(args.get('command', ''))[:200]}` on the HP PC"
    if name == "remote_job":
        return f"{args.get('action', 'manage')} background job '{args.get('name', '')}' on the HP PC"
    if name == "remote_files":
        return f"{args.get('action', 'access')} {str(args.get('path', ''))[:200]} on the HP PC"
    if name == "remote_project_init":
        return f"create project '{args.get('name', '')}' on the HP PC"
    return "check the HP PC over SSH"
