import json

from agent.llm_client import call_llm


def _text_from_response(response) -> str:
    if response is None:
        return "I’m ready to help. Add an Anthropic API key in `.env` to enable the full agent loop."
    return "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def run_agent(user_input: str, conversation_history: list, tools: list, skill_map: dict, max_iterations: int = 10):
    conversation_history.append({"role": "user", "content": user_input})
    for _ in range(max_iterations):
        response = call_llm(conversation_history, tools)
        if response is None:
            return _local_response(user_input, conversation_history), conversation_history
        blocks = getattr(response, "content", [])
        tool_calls = [block for block in blocks if getattr(block, "type", "") == "tool_use"]
        if not tool_calls:
            conversation_history.append({"role": "assistant", "content": blocks})
            return _text_from_response(response), conversation_history
        conversation_history.append({"role": "assistant", "content": blocks})
        results = []
        for call in tool_calls:
            try:
                result = skill_map[call.name](**call.input)
            except Exception as error:  # A broken plugin must not break the session.
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