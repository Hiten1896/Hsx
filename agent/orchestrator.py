import json
from collections.abc import Callable

from agent.ask_user import AskUserRequest
from agent.devices import system_body_context
from agent.llm_client import call_llm
from agent.memory import MemoryManager
from agent.policy import POLICY
from config import MEMORY_DB

MEMORY = MemoryManager(MEMORY_DB)


def _text_from_response(response) -> str:
    if response is None:
        return "I’m ready to help with your local workspace."
    return "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def _serializable_history(history: list) -> list:
    def convert(value):
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        if getattr(value, "type", "") == "tool_use":
            return {
                "type": "tool_use",
                "id": value.id,
                "name": value.name,
                "input": convert(value.input),
            }
        if getattr(value, "type", "") == "text":
            return {"type": "text", "text": value.text}
        if hasattr(value, "__dict__"):
            return {key: convert(item) for key, item in value.__dict__.items()}
        return value

    return convert(history)


def run_agent(
    user_input: str,
    conversation_history: list,
    tools: list,
    skill_map: dict,
    max_iterations: int = 10,
    session_id: str = "default",
    approvals: dict | None = None,
    sensitive: bool = False,
    resume_tool_result: dict | None = None,
    target_device: str = "client_browser",
    device_actions: list[dict] | None = None,
    on_text_delta: Callable[[str], None] | None = None,
):
    if target_device not in {"local_host", "client_browser"}:
        raise ValueError("target_device must be local_host or client_browser.")
    if resume_tool_result:
        conversation_history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": resume_tool_result["tool_call_id"],
                "content": json.dumps({"success": True, "answer": resume_tool_result["answer"]}),
            }],
        })
    else:
        MEMORY.record_message(session_id, "user", user_input)
        conversation_history.append({"role": "user", "content": user_input})
    for _ in range(max_iterations):
        if POLICY.is_stopped():
            return "This run was stopped by the kill switch.", conversation_history
        response = call_llm(
            conversation_history,
            tools,
            MEMORY.prompt_context(user_input),
            sensitive=sensitive,
            target_device=target_device,
            body_context=system_body_context(target_device),
            on_text_delta=on_text_delta,
        )
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
                tool_input = dict(call.input)
                if call.name in {"audio_play", "camera_record"}:
                    tool_input["target_device"] = target_device
                result = skill_map[call.name](**tool_input)
                if isinstance(result, dict) and call.name in {"audio_play", "camera_record"} and device_actions is not None:
                    device_actions.append(
                        result.get("browser_action")
                        or {
                            "type": call.name,
                            "target_device": target_device,
                            "success": result.get("success", False),
                            "message": result.get("message", ""),
                            "path": result.get("path"),
                            "pid": result.get("pid"),
                        }
                    )
            except AskUserRequest as question:
                question.checkpoint = _serializable_history(conversation_history)
                question.tool_call_id = call.id
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                if call.name in {"audio_play", "camera_record"} and device_actions is not None:
                    device_actions.append({
                        "type": call.name,
                        "target_device": target_device,
                        "success": False,
                        "error": str(error),
                    })
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
    return "I can answer directly here or create files locally in your workspace."
