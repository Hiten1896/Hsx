import json
from pathlib import Path

APPROVAL_LOG_PATH = Path.home() / "hsx" / "data" / "approvals.jsonl"
APPROVAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

class ApprovalRouter:
    @staticmethod
    def log_request(tool_name: str, arguments: dict) -> str:
        record = {"tool": tool_name, "arguments": arguments, "status": "pending"}
        with APPROVAL_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return "pending"

    @staticmethod
    def evaluate_action(tool_name: str, arguments: dict) -> bool:
        """Checks if a dynamic tool action requires human approval."""
        sensitive_tools = {"shell", "system_execute", "docker_run", "ingest_github_repo"}
        if tool_name in sensitive_tools:
            ApprovalRouter.log_request(tool_name, arguments)
            return False # Pause for UI approval
        return True