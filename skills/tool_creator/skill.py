"""Policy-gated creation and live registration of small generated skills."""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

from agent.skill_loader import register_runtime_skill
from config import BASE_DIR, SCRATCH_DIR


_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_ALLOWED_IMPORTS = {"json", "math", "re", "datetime", "typing"}
_BANNED_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "__builtins__",
    "subprocess", "socket", "ctypes", "os", "sys", "shutil", "requests", "urllib",
}
_BANNED_ATTRIBUTES = {
    "write_text", "write_bytes", "unlink", "rmdir", "mkdir", "rename", "replace",
    "chmod", "chown", "system", "popen", "run", "Popen",
}


def _validate_code(code: str) -> None:
    if not code.strip() or len(code) > 40_000:
        raise ValueError("Generated code must be non-empty and under 40,000 characters.")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        raise ValueError(f"Generated skill has a syntax error: {error.msg}") from error
    if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run" for node in tree.body):
        raise ValueError("Generated skill must define a top-level run function.")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(name not in _ALLOWED_IMPORTS for name in names):
                raise ValueError("Generated skill imports a restricted module.")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] not in _ALLOWED_IMPORTS:
                raise ValueError("Generated skill imports a restricted module.")
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            raise ValueError(f"Generated skill uses restricted name: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _BANNED_ATTRIBUTES:
            raise ValueError(f"Generated skill uses restricted operation: {node.attr}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("Generated skill cannot access dunder attributes.")
        elif isinstance(node, ast.While):
            raise ValueError("Generated skill cannot use while loops.")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError("Generated skill cannot mutate global state.")


def _validate_metadata(name: str, description: str, input_schema: dict, risk: str) -> None:
    if not _NAME.fullmatch(name):
        raise ValueError("Tool name must be 3-40 lowercase letters, numbers, or underscores.")
    if name in {"tool_creator", "system_execute", "shell"}:
        raise ValueError("This tool name is reserved.")
    if not description.strip() or len(description) > 500:
        raise ValueError("Tool description is required and must be under 500 characters.")
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        raise ValueError("input_schema must be a JSON object schema.")
    if risk not in {"safe", "confirm"}:
        raise ValueError("Generated tools may only use safe or confirm risk levels.")
    json.dumps(input_schema)


def _write_skill(destination: Path, name: str, description: str, code: str, input_schema: dict, risk: str) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "skill.py").write_text(code.rstrip() + "\n", encoding="utf-8")
    metadata = {
        "name": name,
        "description": description,
        "risk": risk,
        "advisory": "This generated tool may perform the requested bounded operation.",
        "requires_acknowledgement": risk == "confirm",
        "input_schema": input_schema,
    }
    (destination / "skill.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def inspect_task(task: str, available_tools: list[str] | tuple[str, ...] = ()) -> dict:
    """Return a deterministic missing-capability result for the agent to act on."""
    words = {word.lower() for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", task)}
    known = {str(name).lower() for name in available_tools}
    missing = not bool(words & known)
    return {"missing": missing, "task": task, "available_tools": sorted(known)}


def run(
    task: str,
    name: str,
    description: str,
    code: str,
    input_schema: dict,
    risk: str = "confirm",
    permanent: bool = False,
) -> dict:
    _validate_metadata(name, description, input_schema, risk)
    _validate_code(code)
    skills_root = BASE_DIR / "skills"
    destination = (skills_root if permanent else SCRATCH_DIR / "dynamic_skills") / name
    if destination.exists():
        raise FileExistsError(f"A generated skill already exists: {name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_skill(destination, name, description, code, input_schema, risk)
    try:
        tool = register_runtime_skill(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "success": True,
        "task": task,
        "tool": tool,
        "path": str(destination),
        "permanent": permanent,
        "registered": True,
    }
