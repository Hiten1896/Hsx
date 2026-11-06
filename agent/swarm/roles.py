from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable

MAX_PROPOSAL_CHARS = 12_000
PYTHON_BLOCK = re.compile(r"```python\s*(.*?)```", re.IGNORECASE | re.DOTALL)
UNFENCED_CODE = re.compile(r"(?m)^\s*\+?\s*(?:import\s|from\s+\S+\s+import\s|def\s+\w+\s*\(|class\s+\w+)")
FORBIDDEN_IMPORTS = {"subprocess", "socket", "ctypes", "sys"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "input", "getattr"}
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


def _validate_python_blocks(proposal: str) -> None:
    sources = PYTHON_BLOCK.findall(proposal)
    if not sources and UNFENCED_CODE.search(proposal):
        raise ValueError("Coder proposal must place Python source in a fenced python block.")
    for source in sources:
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as error:
            raise ValueError(f"Coder proposal contains invalid Python: {error.msg}") from error
        forbidden_os_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                forbidden_os_names.update(
                    name
                    for alias in node.names
                    for name in (alias.name, alias.asname or alias.name)
                )
        if forbidden_os_names & FORBIDDEN_ATTRIBUTES:
            raise ValueError("Coder proposal uses a restricted system or filesystem operation.")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".", 1)[0] in FORBIDDEN_IMPORTS for alias in node.names):
                    raise ValueError("Coder proposal imports a restricted system module.")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".", 1)[0] in FORBIDDEN_IMPORTS:
                    raise ValueError("Coder proposal imports a restricted system module.")
            elif isinstance(node, ast.Call):
                function_name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if function_name in FORBIDDEN_CALLS:
                    raise ValueError("Coder proposal uses a restricted dynamic-code operation.")
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise ValueError("Coder proposal uses a restricted dunder attribute.")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
                raise ValueError("Coder proposal uses a restricted system or filesystem operation.")


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
