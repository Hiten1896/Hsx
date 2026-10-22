from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Specialist:
    name: str
    prompt: str
    allowed_tools: frozenset[str]
    max_depth: int = 2


SPECIALISTS = {
    "researcher": Specialist("researcher", "Research using read-only sources.", frozenset({"read_file", "web_fetch", "web_search"})),
    "coder": Specialist("coder", "Write and test code only in scratch.", frozenset({"run_code"})),
    "docs": Specialist("docs", "Create documents only in outputs.", frozenset({"make_docx", "make_markdown", "make_csv"})),
    "ops": Specialist("ops", "Inspect status and jobs without destructive actions.", frozenset({"system_status", "list_files"})),
}


def delegate(name: str, task: str, tool_runner: Callable[[str, dict[str, Any]], dict], depth: int = 0) -> dict:
    specialist = SPECIALISTS.get(name)
    if specialist is None:
        return {"success": False, "error": "Unknown specialist."}
    if depth >= specialist.max_depth:
        return {"success": False, "error": "Sub-agent depth limit reached."}
    result = tool_runner("system_status" if name == "ops" else "list_files", {})
    return {"success": True, "specialist": specialist.name, "task": task, "result": result, "untrusted": True}
