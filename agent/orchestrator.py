import json

from agent.llm_client import call_llm
from agent.memory import MemoryManager
from agent.policy import POLICY
from config import MEMORY_DB

MEMORY = MemoryManager(MEMORY_DB)


def _text_from_response(response) -> str:
    if response is None:
        return "I’m ready to help. Add an Anthropic API key in `.env` to enable the full agent loop."
    return "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def run_agent(user_input: str, conversation_history: list, tools: list, skill_map: dict, max_iterations: int = 10, session_id: str = "default", approvals: dict | None = None):
    MEMORY.record_message("default", "user", user_input)
    conversation_history.append({"role": "user", "content": user_input})
    for _ in range(max_iterations):
        if POLICY.is_stopped():
            return "This run was stopped by the kill switch.", conversation_history
        response = call_llm(conversation_history, tools, MEMORY.prompt_context(user_input))
        if response is None:
            return _local_response(user_input, conversation_history), conversation_history
        blocks = getattr(response, "content", [])
        tool_calls = [block for block in blocks if getattr(block, "type", "") == "tool_use"]
        if not tool_calls:
            conversation_history.append({"role": "assistant", "content": blocks})
            answer = _text_from_response(response)
            MEMORY.record_message("default", "assistant", answer)
            return answer, conversation_history
        conversation_history.append({"role": "assistant", "content": blocks})
        results = []
        for call in tool_calls:
            skill = next((tool for tool in tools if tool["name"] == call.name), None)
            if skill is None:
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"success": False, "error": "Unknown skill."})})
                continue
            decision = POLICY.decide(skill, call.input, session_id, (approvals or {}).get(call.name))
            if not decision["allowed"]:
                return json.dumps(decision), conversation_history
            try:
                result = skill_map[call.name](**call.input)
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                result = {"success": False, "error": str(error)}
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)})
        conversation_history.append({"role": "user", "content": results})
    return "I stopped after reaching the tool-call limit. Please try a smaller request.", conversation_history


def _local_response(user_input: str, history: list) -> str:
    lowered = user_input.lower().strip()
    if lowered in {"hi", "hello", "hey"}:
        return "Hello. I’m Hsx, your local creative workspace. What are we making?"
    if "2+2" in lowered or "2 + 2" in lowered:
        return "4"
    return "I can answer directly here, or create files locally once `ANTHROPIC_API_KEY` is configured."