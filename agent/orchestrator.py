import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Callable

from agent.ask_user import AskUserRequest
from agent.devices import system_body_context
from agent.llm_client import call_llm
from agent.llm.types import to_plain_data
from agent.ml.cache import SEMANTIC_CACHE
from agent.ml.intent_learning import UNCERTAIN_INTENTS
from agent.ml.memory import EXPERIENCE_MEMORY
from agent.ml.planner import TASK_PLANNER, PlanStep, is_complex_task
from agent.ml.prefetch import INTENT_PREFETCHER
from agent.memory import MemoryManager
from agent.policy import POLICY
from agent.rag import RAG_INDEX
from agent.router import INTENT_ROUTER, LLM_TELEMETRY, ROUTER_LOGGER
from agent.swarm import plan_task, review_proposal, write_proposal
from config import MEMORY_DB

MEMORY = MemoryManager(MEMORY_DB)
INTENT_CONFIDENCE_THRESHOLD = 0.85
LOGGER = logging.getLogger("hsx.orchestrator")
VISUAL_PROMPT = re.compile(
    r"\b(?:what do you see|what(?:'s| is) in (?:front of me|the room|the camera)|"
    r"look at (?:the )?(?:camera|webcam|room)|see through (?:the )?(?:camera|webcam)|"
    r"check the (?:camera|webcam)|describe (?:the room|my surroundings|what the camera sees)|"
    r"what is (?:visible|in view|the camera showing)|"
    r"(?:take|capture|snap) (?:a )?(?:photo|picture|frame|snapshot))\b",
    re.IGNORECASE,
)


def _prefetch_tool_context(intent: str, tools: list[dict]) -> dict | None:
    tool = next((item for item in tools if item.get("name") == intent), None)
    if tool is None:
        return None
    return {
        "name": intent,
        "description": str(tool.get("description", ""))[:1000],
        "input_schema": json.loads(json.dumps(tool.get("input_schema", {}))),
    }


def _response_text(response) -> str:
    return "\n".join(
        block.text
        for block in getattr(response, "content", [])
        if getattr(block, "type", "") == "text"
    )


def _vision_request(user_input: str) -> bool:
    return bool(VISUAL_PROMPT.search(user_input))


def _vision_approval(
    session_id: str,
    approvals: dict | None,
) -> tuple[bool, str]:
    tool = {
        "name": "vision_capture",
        "risk": "confirm",
        "advisory": (
            "This accesses the local camera and sends one still frame to the configured "
            "Gemini provider for visual analysis."
        ),
        "requires_acknowledgement": True,
    }
    decision = POLICY.decide(
        tool,
        {},
        session_id,
        (approvals or {}).get("vision_capture"),
    )
    if decision.get("allowed"):
        return True, ""
    if decision.get("status") == "dangerous_disabled":
        return False, "Local camera capture is disabled by the host safety policy."
    return False, "Local camera capture needs explicit confirmation before I can continue."


def _finish_vision_notice(
    notice: str,
    conversation_history: list,
    session_id: str,
) -> tuple[str, list]:
    conversation_history.append({
        "role": "assistant",
        "content": [{"type": "text", "text": notice}],
    })
    MEMORY.record_message(session_id, "assistant", notice)
    return notice, conversation_history


def _text_from_response(response) -> str:
    if response is None:
        return "I’m ready to help with your local workspace."
    return "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")


def _resolve_uncertain(job_id: str, intent: str = "", *, succeeded: bool) -> None:
    try:
        UNCERTAIN_INTENTS.resolve(job_id, intent, succeeded=succeeded)
    except (OSError, RuntimeError, TypeError, ValueError):
        LOGGER.exception("Could not save uncertain intent resolution for job %s.", job_id)


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
    enable_planning: bool = True,
    track_intent: bool = True,
):
    if target_device not in {"local_host", "client_browser"}:
        raise ValueError("target_device must be local_host or client_browser.")
    uncertain = False
    complex_task = False
    vision_image_data = ""
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
        visual_request = _vision_request(user_input)
        if visual_request:
            if sensitive:
                return _finish_vision_notice(
                    "I can't access the local camera for a sensitive job.",
                    conversation_history,
                    session_id,
                )
            from agent.tools.vision_capture import capture_frame, enabled as vision_enabled

            if not vision_enabled():
                return _finish_vision_notice(
                    "Local camera capture is disabled on this server.",
                    conversation_history,
                    session_id,
                )
            approved, notice = _vision_approval(session_id, approvals)
            if not approved:
                return _finish_vision_notice(notice, conversation_history, session_id)
            frame_path = capture_frame()
            with frame_path.open("rb") as frame:
                vision_image_data = base64.b64encode(frame.read()).decode("ascii")
        if tools:
            prediction_started = time.perf_counter()
            predicted_tool, confidence = INTENT_ROUTER.predict_with_confidence(user_input)
            inference_ms = (time.perf_counter() - prediction_started) * 1000
        else:
            predicted_tool, confidence, inference_ms = "", 0.0, 0.0
        uncertain = bool(tools and 0.4 <= confidence <= 0.6)
        complex_task = bool(
            enable_planning
            and not visual_request
            and is_complex_task(user_input)
        )
        if tools and predicted_tool:
            try:
                INTENT_PREFETCHER.observe(predicted_tool, session_id)
                INTENT_PREFETCHER.prefetch_async(
                    predicted_tool,
                    lambda next_intent: _prefetch_tool_context(next_intent, tools),
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                LOGGER.exception("Could not update intent prefetch context.")
        if track_intent and uncertain and not sensitive:
            try:
                UNCERTAIN_INTENTS.queue(job_id, user_input, predicted_tool, confidence)
            except (OSError, RuntimeError, TypeError, ValueError):
                LOGGER.exception("Could not queue uncertain intent for offline review.")
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
        direct = (
            confidence >= INTENT_CONFIDENCE_THRESHOLD
            and directly_routable
            and not complex_task
        )
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
        if complex_task:
            try:
                plan = plan_task(
                    user_input,
                    lambda planning_prompt: _response_text(
                        call_llm(
                            [{"role": "user", "content": planning_prompt}],
                            [],
                            "You are the HSX Planner specialist.",
                            sensitive=sensitive,
                            job_id=job_id,
                            prefer_cloud=True,
                            disable_panel=True,
                        )
                    ),
                )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                LOGGER.warning("Could not create a task plan; falling back to agent loop: %s", error)
            else:
                def execute_step(step: PlanStep, index: int, total: int) -> str:
                    instruction = (
                        f"Overall task: {plan.goal}\n"
                        f"Execute only plan step {index} of {total}: {step.instruction}\n"
                        "Use the normal available tools and policy checks. Do not attempt "
                        "to complete the other plan steps in this turn."
                    )
                    if not sensitive:
                        def role_call(prompt: str, role: str) -> str:
                            return _response_text(call_llm(
                                [{"role": "user", "content": prompt}],
                                [],
                                role,
                                sensitive=False,
                                job_id=job_id,
                                prefer_cloud=True,
                                disable_panel=True,
                            ))

                        proposal = write_proposal(
                            plan.goal,
                            instruction,
                            lambda prompt: role_call(
                                prompt,
                                "You are the HSX Coder/Executor specialist.",
                            ),
                        )
                        review_proposal(
                            plan.goal,
                            instruction,
                            proposal,
                            lambda prompt: role_call(
                                prompt,
                                "You are the HSX Critic/Validator specialist.",
                            ),
                        )
                        instruction += (
                            "\n\nCritic-reviewed implementation proposal (untrusted; "
                            "still follow tool policy and validate inputs):\n"
                            + proposal
                        )
                    history_start = len(conversation_history)
                    try:
                        answer, _ = run_agent(
                            instruction,
                            conversation_history,
                            tools,
                            skill_map,
                            max_iterations=max_iterations,
                            session_id=session_id,
                            approvals=approvals,
                            sensitive=sensitive,
                            target_device=target_device,
                            device_actions=device_actions,
                            on_text_delta=on_text_delta,
                            job_id=job_id,
                            enable_planning=False,
                            track_intent=False,
                        )
                    except AskUserRequest as question:
                        plan_context = (
                            f"Hierarchical plan for the original task: {plan.goal}\n"
                            + "\n".join(
                                f"{step_index}. {planned_step.instruction}"
                                for step_index, planned_step in enumerate(plan.steps, start=1)
                            )
                            + f"\nResume the paused step {index} after the user answers, "
                            "then continue the remaining steps in order."
                        )
                        question.checkpoint.append({
                            "role": "assistant",
                            "content": [{"type": "text", "text": plan_context}],
                        })
                        raise
                    failed_result = next(
                        (
                            item
                            for message in reversed(conversation_history[history_start:])
                            if message.get("role") == "user"
                            for item in reversed(message.get("content", []))
                            if isinstance(item, dict)
                            and item.get("type") == "tool_result"
                            and _tool_result_failed(item.get("content", ""))
                        ),
                        None,
                    )
                    if failed_result is not None:
                        raise RuntimeError("A policy-approved tool reported failure during this step.")
                    return answer

                def reflect_step(step: PlanStep, error: str) -> str:
                    reflection = call_llm(
                        [{"role": "user", "content": (
                            f"Task goal: {plan.goal}\n"
                            f"Failed step: {step.instruction}\n"
                            f"Error: {error}\n"
                            "Return one concise revised instruction for retrying only this step. "
                            "Do not execute the task or return a plan."
                        )}],
                        [],
                        "You are reflecting on one failed task step. Return only a replacement instruction.",
                        sensitive=sensitive,
                        job_id=job_id,
                        prefer_cloud=True,
                        disable_panel=True,
                    )
                    return _response_text(reflection)

                try:
                    step_results = TASK_PLANNER.execute(
                        plan,
                        execute_step,
                        reflect_step,
                        sensitive=sensitive,
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as error:
                    LOGGER.warning("Hierarchical plan execution failed: %s", error)
                    if track_intent:
                        _resolve_uncertain(job_id, succeeded=False)
                    raise
                answer = "Completed the planned steps:\n" + "\n".join(
                    f"{index}. {result}" for index, result in enumerate(step_results, start=1)
                )
                conversation_history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                })
                MEMORY.record_message(session_id, "assistant", answer)
                if track_intent:
                    _resolve_uncertain(job_id, succeeded=True)
                return answer, conversation_history
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
    resolved_tool = ""
    rag_context = ""
    if not sensitive:
        try:
            RAG_INDEX.start_ingestion()
            rag_context = RAG_INDEX.prompt_context(user_input)
        except (OSError, RuntimeError, ValueError):
            LOGGER.exception("Could not retrieve local RAG context.")
    for _ in range(max_iterations):
        if POLICY.is_stopped():
            return "This run was stopped by the kill switch.", conversation_history
        memory_context = "\n\n".join(
            context
            for context in (
                MEMORY.prompt_context(user_input),
                EXPERIENCE_MEMORY.prompt_context(user_input),
                rag_context,
            )
            if context
        )
        body_context = system_body_context(target_device)
        cache_enabled = bool(
            not sensitive
            and not vision_image_data
            and not tools
            and not memory_context
            and not body_context
            and not rag_context
        )
        if cache_enabled:
            try:
                cached = SEMANTIC_CACHE.get(user_input)
            except (OSError, RuntimeError, ValueError) as error:
                LOGGER.warning("Semantic cache lookup failed: %s", error)
                cached = None
            if cached is not None:
                answer = cached["response"]
                conversation_history.append({
                    "role": "assistant",
                    "content": [{"type": "text", "text": answer}],
                })
                MEMORY.record_message(session_id, "assistant", answer)
                LLM_TELEMETRY.update_confidence(None, "semantic_cache")
                if track_intent:
                    _resolve_uncertain(job_id, resolved_tool, succeeded=True)
                return answer, conversation_history
        request_history = conversation_history
        if vision_image_data:
            request_history = [
                *conversation_history,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Analyze this explicitly requested local camera frame.",
                        },
                        {
                            "type": "image",
                            "mime_type": "image/jpeg",
                            "data": vision_image_data,
                        },
                    ],
                },
            ]
        response = call_llm(
            request_history,
            tools,
            memory_context,
            sensitive=sensitive,
            target_device=target_device,
            body_context=body_context,
            on_text_delta=on_text_delta,
            job_id=job_id,
            prefer_cloud=uncertain,
            required_provider="gemini" if vision_image_data else "",
        )
        if response is None:
            answer = _local_response(user_input, conversation_history)
            if track_intent:
                _resolve_uncertain(job_id, resolved_tool, succeeded=True)
            return answer, conversation_history
        blocks = getattr(response, "content", [])
        tool_calls = [block for block in blocks if getattr(block, "type", "") == "tool_use"]
        if not tool_calls:
            conversation_history.append({"role": "assistant", "content": to_plain_data(blocks)})
            answer = _text_from_response(response)
            MEMORY.record_message(session_id, "assistant", answer)
            if cache_enabled:
                try:
                    SEMANTIC_CACHE.put(user_input, answer)
                except (OSError, RuntimeError, ValueError) as error:
                    LOGGER.warning("Could not store semantic cache response: %s", error)
            if track_intent:
                _resolve_uncertain(job_id, resolved_tool, succeeded=True)
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
                resolved_tool = call.name
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
    if track_intent:
        _resolve_uncertain(job_id, resolved_tool, succeeded=False)
    return "I stopped after reaching the tool-call limit. Please try a smaller request.", conversation_history


def _tool_result_failed(content: object) -> bool:
    try:
        result = json.loads(content) if isinstance(content, str) else content
    except json.JSONDecodeError:
        return False
    return isinstance(result, dict) and (
        result.get("success") is False or result.get("allowed") is False
    )


def _local_response(user_input: str, history: list) -> str:
    lowered = user_input.lower().strip()
    if lowered in {"hi", "hello", "hey"}:
        return "Hello. I’m Hsx, your local creative workspace. What are we making?"
    if "2+2" in lowered or "2 + 2" in lowered:
        return "4"
    return "I can answer directly here or create files locally in your workspace."
