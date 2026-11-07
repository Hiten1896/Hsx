from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable

MAX_PROPOSAL_CHARS = 12_000
PYTHON_BLOCK = re.compile(r"```python\s*(.*?)```", re.IGNORECASE | re.DOTALL)
UNFENCED_CODE = re.compile(r"(?m)^\s*\+?\s*(?:import\s|from\s+\S+\s+import\s|def\s+\w+\s*\(|class\s+\w+)")
FORBIDDEN_IMPORTS = {"subprocess", "socket", "ctypes", "sys"}
FORBIDDEN_CALLS = {"__import__", "open", "input", "globals", "locals", "vars"}
FORBIDDEN_NAMES = {"__builtins__"}
FORBIDDEN_ATTRIBUTES = {
    "unlink", "rmdir", "write_text", "write_bytes", "rename", "replace",
    "chmod", "chown", "system", "popen", "run", "Popen", "remove",
    "getenv", "putenv", "unsetenv", "environ", "execv", "execve",
    "execl", "execle", "execlp", "execlpe", "execvp", "execvpe", "fork",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve",
    "spawnvp", "spawnvpe", "startfile",
}


def write_proposal(
    goal: str,
    step: str,
    llm_call: Callable[[str], str],
) -> str:
    prompt = (
        "You are the Coder/Executor specialist. Propose a small, reviewable implementation "
        "for one task step. Do not execute it or claim files were changed. If code is needed, "
        "include a Python code block and focused tests. Do not include secrets.\n"
        f"Overall goal: {goal[:1000]}\nTask step: {step[:1000]}"
    )
    proposal = llm_call(prompt)
    if not isinstance(proposal, str) or not proposal.strip():
        raise ValueError("Coder specialist returned an empty proposal.")
    return proposal.strip()[:MAX_PROPOSAL_CHARS]


def _literal_code_source(node: ast.AST, default_mode: str) -> tuple[str, str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, default_mode
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "compile"
        and len(node.args) >= 3
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and isinstance(node.args[2], ast.Constant)
        and node.args[2].value in {"exec", "eval", "single"}
    ):
        return node.args[0].value, node.args[2].value
    return None


def _validate_python_tree(tree: ast.AST, dynamic_depth: int = 0) -> None:
    forbidden_os_names = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
        for name in (alias.name, alias.asname or alias.name)
    }
    if forbidden_os_names & FORBIDDEN_ATTRIBUTES:
        raise ValueError("Coder proposal uses a restricted system or filesystem operation.")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(module.split(".", 1)[0] in FORBIDDEN_IMPORTS for module in modules):
                raise ValueError("Coder proposal imports a restricted system module.")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ValueError("Coder proposal uses a restricted dunder name.")
        elif isinstance(node, ast.Call):
            function_name = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else ""
            )
            if function_name in {"eval", "exec", "compile"}:
                if dynamic_depth >= 5 or not isinstance(node.func, ast.Name) or not node.args:
                    raise ValueError("Coder proposal uses a restricted dynamic-code operation.")
                default_mode = "eval" if function_name == "eval" else "exec"
                code_source = _literal_code_source(node.args[0], default_mode)
                if function_name == "compile":
                    code_source = _literal_code_source(node, default_mode)
                if code_source is None:
                    raise ValueError("Coder proposal uses a restricted dynamic-code operation.")
                source, mode = code_source
                try:
                    nested_tree = ast.parse(source, mode=mode)
                except SyntaxError as error:
                    raise ValueError("Coder proposal contains invalid generated Python.") from error
                _validate_python_tree(nested_tree, dynamic_depth + 1)
            elif function_name == "getattr":
                if (
                    not isinstance(node.func, ast.Name)
                    or len(node.args) < 2
                    or not isinstance(node.args[1], ast.Constant)
                    or not isinstance(node.args[1].value, str)
                    or node.args[1].value.startswith("__")
                    or node.args[1].value in FORBIDDEN_ATTRIBUTES
                ):
                    raise ValueError("Coder proposal uses a restricted dynamic-code operation.")
            elif function_name in FORBIDDEN_CALLS:
                raise ValueError("Coder proposal uses a restricted dynamic-code operation.")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("Coder proposal uses a restricted dunder attribute.")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            raise ValueError("Coder proposal uses a restricted system or filesystem operation.")


def _validate_python_blocks(proposal: str) -> None:
    sources = PYTHON_BLOCK.findall(proposal)
    if not sources and UNFENCED_CODE.search(proposal):
        raise ValueError("Coder proposal must place Python source in a fenced python block.")
    for source in sources:
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as error:
            raise ValueError(f"Coder proposal contains invalid Python: {error.msg}") from error
        _validate_python_tree(tree)


def review_proposal(
    goal: str,
    step: str,
    proposal: str,
    llm_call: Callable[[str], str],
) -> dict:
    _validate_python_blocks(proposal)
    prompt = (
        "You are the Critic/Validator specialist. Review this proposed change before it is "
        "passed to the normal HSX executor. The executor still enforces its existing tool and "
        "policy checks. Reject destructive, credential-seeking, network-exfiltration, or "
        "out-of-scope behavior. Assess whether focused tests are included or clearly needed. "
        'Return JSON only: {"approved":true|false,"tests_reviewed":true|false,'
        '"rationale":"short reason"}. Approve only a safe, scoped proposal.\n'
        f"Goal: {goal[:1000]}\nStep: {step[:1000]}\n"
        f"Proposal:\n{proposal[:MAX_PROPOSAL_CHARS]}"
    )
    raw = llm_call(prompt)
    try:
        decision = json.loads(raw.strip())
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Critic specialist returned an invalid review.") from error
    if (
        not isinstance(decision, dict)
        or decision.get("approved") is not True
        or decision.get("tests_reviewed") is not True
    ):
        rationale = decision.get("rationale", "") if isinstance(decision, dict) else ""
        raise ValueError(f"Critic specialist did not approve the proposal: {str(rationale)[:300]}")
    return {
        "approved": True,
        "tests_reviewed": True,
        "rationale": str(decision.get("rationale", ""))[:500],
    }
