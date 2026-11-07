from __future__ import annotations

import ast
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

from agent.ml.storage import atomic_write_json
from config import DATA_DIR

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
RESERVED_NAMES = {"tool_creator", "system_execute", "shell", "create_dynamic_tool"}


def _normalize_text(**arguments: Any) -> dict:
    text = arguments.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    return {"success": True, "text": " ".join(text.split())}


def _count_words(**arguments: Any) -> dict:
    text = arguments.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    return {"success": True, "count": len(text.split())}


def _sha256_text(**arguments: Any) -> dict:
    text = arguments.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    return {"success": True, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


OPERATIONS: dict[str, tuple[Callable[..., dict], dict[str, Any], dict[str, Any]]] = {
    "normalize_text": (
        _normalize_text,
        {"text": "  hello   world  "},
        {"success": True, "text": "hello world"},
    ),
    "count_words": (
        _count_words,
        {"text": "one two three"},
        {"success": True, "count": 3},
    ),
    "sha256_text": (_sha256_text, {"text": "hsx"}, None),
}


class DynamicToolRegistry:
    """Persist and register generated declarations backed by vetted pure functions.

    Arbitrary source is deliberately not imported or executed. New capabilities are
    declarations that select one of the implementation templates in OPERATIONS.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "dynamic_tools.json"
        self._lock = threading.RLock()
        self._tools: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _validate_source_template(operation: str) -> None:
        source = f"def run(**arguments):\n    return {operation}(**arguments)\n"
        tree = ast.parse(source, mode="exec")
        if (
            len(tree.body) != 1
            or not isinstance(tree.body[0], ast.FunctionDef)
            or tree.body[0].name != "run"
        ):
            raise ValueError("Generated tool template failed syntax validation.")
        if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)):
            raise ValueError("Generated tool template contains a forbidden operation.")

    @staticmethod
    def _validate_spec(name: str, description: str, operation: str, input_schema: dict) -> None:
        if not NAME_PATTERN.fullmatch(name) or name in RESERVED_NAMES:
            raise ValueError("Tool name is invalid or reserved.")
        if not isinstance(description, str) or not description.strip() or len(description) > 500:
            raise ValueError("description must contain 1-500 characters.")
        if operation not in OPERATIONS:
            raise ValueError("Only vetted pure-function operations can be registered.")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise ValueError("input_schema must be an object schema.")
        properties = input_schema.get("properties", {})
        if not isinstance(properties, dict) or set(properties) - {"text"}:
            raise ValueError("Dynamic tools may accept only the text string argument.")
        if "text" in properties:
            if not isinstance(properties["text"], dict) or properties["text"].get("type") != "string":
                raise ValueError("The text property must have string type.")
        if input_schema.get("required", []) not in ([], ["text"]):
            raise ValueError("Only text may be required.")
        json.dumps(input_schema)

    def create(
        self,
        *,
        task: str,
        name: str,
        description: str,
        operation: str,
        input_schema: dict,
        tools: list,
        skill_map: dict,
    ) -> dict:
        if not isinstance(task, str) or not task.strip() or len(task) > 500:
            raise ValueError("task must contain 1-500 characters.")
        self._validate_spec(name, description, operation, input_schema)
        self._validate_source_template(operation)
        runner, sample_arguments, expected = OPERATIONS[operation]
        sample_result = runner(**sample_arguments)
        if expected is not None and sample_result != expected:
            raise RuntimeError(f"Vetted implementation self-test failed for {operation}.")
        if operation == "sha256_text" and (
            not isinstance(sample_result.get("sha256"), str) or len(sample_result["sha256"]) != 64
        ):
            raise RuntimeError("The SHA-256 implementation self-test failed.")
        with self._lock:
            if name in skill_map or name in self._tools or any(
                item.get("name") == name for item in tools
            ):
                raise ValueError(f"Tool already exists: {name}")
            entry = {
                "name": name,
                "description": description.strip(),
                "operation": operation,
                "input_schema": input_schema,
            }
            all_tools = [*self._tools.values(), entry]
            atomic_write_json(self.path, {"version": 1, "tools": all_tools})
            tools.append({key: value for key, value in entry.items() if key != "operation"})
            skill_map[name] = runner
            self._tools[name] = entry
        return {
            "success": True,
            "task": task[:500],
            "tool": name,
            "operation": operation,
            "registered": True,
            "source_executed": False,
        }

    def restore(self, tools: list, skill_map: dict) -> int:
        if not self.path.is_file():
            return 0
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tools"), list):
            raise ValueError("Dynamic tool registry file has an unsupported format.")
        prepared = []
        seen_names: set[str] = set()
        for item in data["tools"]:
            if not isinstance(item, dict):
                raise ValueError("Dynamic tool registry entries must be objects.")
            name, description, operation, schema = (
                item.get("name"), item.get("description"), item.get("operation"), item.get("input_schema")
            )
            if not all(isinstance(value, str) for value in (name, description, operation)):
                raise ValueError("Dynamic tool registry entry has invalid text fields.")
            self._validate_spec(name, description, operation, schema)
            self._validate_source_template(operation)
            if name in seen_names:
                raise ValueError(f"Duplicate dynamic tool name: {name}")
            seen_names.add(name)
            if name in skill_map or any(existing.get("name") == name for existing in tools):
                continue
            runner = OPERATIONS[operation][0]
            _, sample_arguments, expected = OPERATIONS[operation]
            sample_result = runner(**sample_arguments)
            if expected is not None and sample_result != expected:
                raise RuntimeError(f"Vetted implementation self-test failed for {operation}.")
            if operation == "sha256_text" and (
                not isinstance(sample_result.get("sha256"), str) or len(sample_result["sha256"]) != 64
            ):
                raise RuntimeError("The SHA-256 implementation self-test failed.")
            entry = {
                "name": name,
                "description": description,
                "operation": operation,
                "input_schema": schema,
            }
            prepared.append((entry, runner))
        for entry, runner in prepared:
            name = entry["name"]
            tools.append({key: value for key, value in entry.items() if key != "operation"})
            skill_map[name] = runner
            self._tools[name] = entry
        return len(prepared)


DYNAMIC_TOOLS = DynamicToolRegistry()


def register_meta_tool(tools: list, skill_map: dict) -> None:
    """Expose a confirmation-gated declarative tool-creation meta-tool."""
    meta = {
        "name": "create_dynamic_tool",
        "description": (
            "Register a small pure text utility from the vetted operations "
            "normalize_text, count_words, or sha256_text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": sorted(OPERATIONS),
                },
                "input_schema_json": {
                    "type": "string",
                    "description": "JSON-encoded object schema; it may contain a text string property.",
                },
            },
            "required": ["task", "name", "description", "operation", "input_schema_json"],
        },
    }

    def create(**arguments):
        if "input_schema_json" in arguments:
            try:
                arguments["input_schema"] = json.loads(arguments.pop("input_schema_json"))
            except json.JSONDecodeError as error:
                raise ValueError("input_schema_json must contain valid JSON.") from error
        return DYNAMIC_TOOLS.create(
            **arguments,
            tools=tools,
            skill_map=skill_map,
        )

    if "create_dynamic_tool" not in skill_map and not any(
        item.get("name") == "create_dynamic_tool" for item in tools
    ):
        tools.append(meta)
        skill_map["create_dynamic_tool"] = create
