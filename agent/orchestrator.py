import json
import time
import uuid
from collections.abc import Callable

from agent.ask_user import AskUserRequest
from agent.devices import system_body_context
from agent.llm_client import call_llm
from agent.llm.types import to_plain_data
from agent.ml.memory import EXPERIENCE_MEMORY
from agent.memory import MemoryManager
from agent.policy import POLICY
from agent.router import INTENT_ROUTER, LLM_TELEMETRY, ROUTER_LOGGER
from config import MEMORY_DB

MEMORY = MemoryManager(MEMORY_DB)
INTENT_CONFIDENCE_THRESHOLD = 0.85


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
    job_id: str = "",
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
        if tools:
            prediction_started = time.perf_counter()
            predicted_tool, confidence = INTENT_ROUTER.predict_with_confidence(user_input)
            inference_ms = (time.perf_counter() - prediction_started) * 1000
        else:
            predicted_tool, confidence, inference_ms = "", 0.0, 0.0
        tool = next((item for item in tools if item["name"] == predicted_tool), None)
        schema = tool.get("input_schema", {}) if tool else {}
        directly_routable = bool(
            tool
            and predicted_tool in skill_map
            and tool.get("risk", "confirm") == "safe"
            and not tool.get("requires_acknowledgement", False)
            and not str(tool.get("advisory", "")).strip()
            and not schema.get("required", [])
        )
        direct = confidence >= INTENT_CONFIDENCE_THRESHOLD and directly_routable
        LLM_TELEMETRY.update_confidence(
            confidence if tools else None,
            "direct" if direct else "llm",
        )
        if tools:
            ROUTER_LOGGER.log_intent_prediction(
                session_id,
                job_id,
                predicted_tool,
                confidence,
                inference_ms,
                "direct" if direct else "llm",
            )
        if direct:
            tool_call_id = f"local-{uuid.uuid4().hex}"
            decision = POLICY.decide(tool, {}, session_id, (approvals or {}).get(predicted_tool))
            if not decision["allowed"]:
                ROUTER_LOGGER.log_tool_prediction(
                    session_id, job_id, predicted_tool, tool_call_id
                )
                ROUTER_LOGGER.log_tool_execution(
                    session_id, job_id, predicted_tool, tool_call_id, 0, "denied"
                )
                answer = json.dumps(decision)
                conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
                return answer, conversation_history

            conversation_history.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": tool_call_id,
                    "name": predicted_tool,
                    "input": {},
                }],
            })
            ROUTER_LOGGER.log_tool_prediction(
                session_id, job_id, predicted_tool, tool_call_id
            )
            started = time.perf_counter()
            try:
                result = skill_map[predicted_tool]()
            except AskUserRequest as question:
                ROUTER_LOGGER.log_tool_execution(
                    session_id,
                    job_id,
                    predicted_tool,
                    tool_call_id,
                    (time.perf_counter() - started) * 1000,
                    "needs_input",
                )
                question.checkpoint = _serializable_history(conversation_history)
                question.tool_call_id = tool_call_id
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                result = {"success": False, "error": str(error)}
                status = "failed"
            else:
                status = "succeeded"
            ROUTER_LOGGER.log_tool_execution(
                session_id,
                job_id,
                predicted_tool,
                tool_call_id,
                (time.perf_counter() - started) * 1000,
                status,
            )
            LLM_TELEMETRY.record_local(
                predicted_tool,
                (time.perf_counter() - started) * 1000,
            )
            conversation_history.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }],
            })
            answer = json.dumps(result, ensure_ascii=False, indent=2)
            conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            MEMORY.record_message(session_id, "assistant", answer)
            return answer, conversation_history
    for _ in range(max_iterations):
        if POLICY.is_stopped():
            return "This run was stopped by the kill switch.", conversation_history
        memory_context = "\n\n".join(
            context
            for context in (
                MEMORY.prompt_context(user_input),
                EXPERIENCE_MEMORY.prompt_context(user_input),
            )
            if context
        )
        response = call_llm(
            conversation_history,
            tools,
            memory_context,
            sensitive=sensitive,
            target_device=target_device,
            body_context=system_body_context(target_device),
            on_text_delta=on_text_delta,
            job_id=job_id,
        )
        if response is None:
            return _local_response(user_input, conversation_history), conversation_history
        blocks = getattr(response, "content", [])
        tool_calls = [block for block in blocks if getattr(block, "type", "") == "tool_use"]
        if not tool_calls:
            conversation_history.append({"role": "assistant", "content": to_plain_data(blocks)})
            answer = _text_from_response(response)
            MEMORY.record_message("default", "assistant", answer)
            return answer, conversation_history
        conversation_history.append({"role": "assistant", "content": to_plain_data(blocks)})
        results = []
        for call in tool_calls:
            ROUTER_LOGGER.log_tool_prediction(session_id, job_id, call.name, call.id)
            skill = next((tool for tool in tools if tool["name"] == call.name), None)
            if skill is None:
                ROUTER_LOGGER.log_tool_execution(session_id, job_id, call.name, call.id, 0, "unknown_tool")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"success": False, "error": "Unknown skill."})})
                continue
            decision = POLICY.decide(skill, call.input, session_id, (approvals or {}).get(call.name))
            if not decision["allowed"]:
                ROUTER_LOGGER.log_tool_execution(session_id, job_id, call.name, call.id, 0, "denied")
                return json.dumps(decision), conversation_history
            started = time.perf_counter()
            try:
                tool_input = dict(call.input)
                if call.name in {"audio_play", "camera_record"}:
                    tool_input["target_device"] = target_device
                result = skill_map[call.name](**tool_input)
            except AskUserRequest as question:
                ROUTER_LOGGER.log_tool_execution(
                    session_id, job_id, call.name, call.id, (time.perf_counter() - started) * 1000, "needs_input"
                )
                question.checkpoint = _serializable_history(conversation_history)
                question.tool_call_id = call.id
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                ROUTER_LOGGER.log_tool_execution(
                    session_id, job_id, call.name, call.id, (time.perf_counter() - started) * 1000, "failed"
                )
                if call.name in {"audio_play", "camera_record"} and device_actions is not None:
                    device_actions.append({
                        "type": call.name,
                        "target_device": target_device,
                        "success": False,
                        "error": str(error),
                    })
                result = {"success": False, "error": str(error)}
            else:
                ROUTER_LOGGER.log_tool_execution(
                    session_id, job_id, call.name, call.id, (time.perf_counter() - started) * 1000, "succeeded"
                )
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
