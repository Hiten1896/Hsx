import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from agent.activity_trace import tool_end_event, tool_start_event
from agent.ask_user import ApprovalRequest, AskUserRequest
from agent.devices import system_body_context
from agent.llm_client import call_llm, record_job_tool
from agent.llm.types import to_plain_data
from agent.ml.cache import SEMANTIC_CACHE
from agent.ml.intent_learning import UNCERTAIN_INTENTS
from agent.ml.memory import EXPERIENCE_MEMORY
from agent.ml.planner import TASK_PLANNER, PlanStep, StepState, PlanExecutionError, is_complex_task
from agent.ml.prefetch import INTENT_PREFETCHER
from agent.memory import MemoryManager
from agent.memory.knowledge_graph import KNOWLEDGE_GRAPH
from agent.policy import POLICY
from agent.autonomy import classify_action
from agent.rag import RAG_INDEX
from agent.router import INTENT_ROUTER, LLM_TELEMETRY, ROUTER_LOGGER
from agent.swarm import plan_task, write_proposal_with_repair
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
ATTACHED_IMAGE_PROMPT = re.compile(
    r"\b(?:what(?:'s| is) (?:this|in (?:this|the) (?:image|picture|photo))|"
    r"what does (?:this|the) (?:image|picture|photo) show|"
    r"describe (?:this|the) (?:image|picture|photo|it)|"
    r"identify (?:this|the) (?:image|picture|photo|object)|"
    r"read (?:the )?text (?:in|from) (?:this|the) (?:image|picture|photo))\b",
    re.IGNORECASE,
)
IMAGE_SUFFIXES = {".avif", ".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
CAMERA_START_PROMPT = re.compile(
    r"\b(?:turn on (?:the )?camera|start (?:the )?camera|open (?:the )?camera|show (?:me )?(?:the )?room|activate (?:the )?camera)\b",
    re.IGNORECASE,
)
CAMERA_STOP_PROMPT = re.compile(
    r"\b(?:turn off (?:the )?camera|stop (?:the )?camera|close (?:the )?camera|shut off (?:the )?camera|deactivate (?:the )?camera)\b",
    re.IGNORECASE,
)
PREFERENCE_PROMPT = re.compile(
    r"\b(?:always remember(?: that)?|remember that|remember:|please remember(?: that)?|note that|keep in mind that|standing rule:?)\b",
    re.IGNORECASE,
)
WEB_SEARCH_PROMPT = re.compile(
    r"\b(?:weather|news|latest|current|today|search (?:the )?web|look up|find online|what happened)\b",
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
    from agent.hardware.camera import CAMERA
    from agent.tools.vision_capture import disabled_reason, enabled

    if CAMERA.is_started():
        return True, ""
    if not enabled():
        return False, disabled_reason()
    status = CAMERA.health_check()
    if not status.get("device_available") or status.get("last_error") == "ffmpeg is not installed.":
        return False, disabled_reason()
    tool = {
        "name": "vision_capture",
    }
    decision = POLICY.decide(
        tool,
        {},
        session_id,
        (approvals or {}).get("vision_capture"),
    )
    if decision.get("allowed"):
        return True, ""
    return False, "Starting the camera needs your permission before I can continue."


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


def _image_path_was_named(user_input: str, path: str) -> bool:
    normalized_path = path.strip().replace("\\", "/").casefold()
    normalized_request = user_input.replace("\\", "/").casefold()
    if not normalized_path:
        return False
    return re.search(
        rf"(?<![\w/-])(?<!\w\.){re.escape(normalized_path)}(?![\w/-]|\.\w)",
        normalized_request,
    ) is not None


def _attached_image_paths(user_input: str) -> list[str]:
    marker = "\n\nUploaded workspace files:"
    if marker not in user_input:
        return []
    attachment_list = user_input.split(marker, 1)[1]
    return [
        path
        for line in attachment_list.splitlines()
        if (path := line.strip().removeprefix("- ").strip())
        and Path(path).suffix.casefold() in IMAGE_SUFFIXES
    ]


def _friendly_structured_answer(value: object) -> str:
    """Keep tool payloads out of chat when a model echoes raw JSON."""
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, TypeError):
        return str(value)
    if isinstance(parsed, dict):
        files = parsed.get("files") or parsed.get("results")
        if isinstance(files, list):
            names = [
                str(item.get("name") or item.get("path") or item.get("filename") or "File")
                if isinstance(item, dict) else str(item)
                for item in files
            ]
            if names:
                return "I found these files:\n" + "\n".join(f"• {name}" for name in names)
            return "I couldn't find any matching files."
        if parsed.get("success") is False:
            return str(parsed.get("error") or parsed.get("message") or "I couldn't complete that request.")
        return str(parsed.get("message") or "The request completed, but returned no readable details.")
    if isinstance(parsed, list):
        return "Here are the results:\n" + "\n".join(
            f"• {item.get('name') or item.get('path') or 'Item'}" if isinstance(item, dict) else f"• {item}"
            for item in parsed
        )
    return str(value)


def _friendly_model_answer(answer: str) -> str:
    stripped = answer.strip()
    if not stripped.startswith(("{", "[")):
        return answer
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return answer
    return _friendly_structured_answer(parsed)


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
            result = {
                "type": "tool_use",
                "id": value.id,
                "name": value.name,
                "input": convert(value.input),
            }
            if getattr(value, "provider_meta", None):
                result["provider_meta"] = convert(value.provider_meta)
            if getattr(value, "part", None):
                result["gemini_part"] = convert(value.part)
            return result
        if getattr(value, "type", "") == "text":
            result = {"type": "text", "text": value.text}
            if getattr(value, "provider_meta", None):
                result["provider_meta"] = convert(value.provider_meta)
            return result
        if getattr(value, "type", "") == "gemini_part":
            result = {"type": "gemini_part", "part": convert(value.part or {})}
            if getattr(value, "provider_meta", None):
                result["provider_meta"] = convert(value.provider_meta)
            return result
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
    on_provider_notice: Callable[[str], None] | None = None,
    on_activity_event: Callable[[str, dict], None] | None = None,
    job_id: str = "",
    enable_planning: bool = True,
    track_intent: bool = True,
    cancel_event=None,
):
    def emit_activity_start(tool_name: str, arguments: dict, call_id: str) -> dict:
        if job_id:
            record_job_tool(job_id, tool_name)
        event = tool_start_event(tool_name, arguments, call_id)
        if on_activity_event is not None:
            on_activity_event("tool_start", event)
        return event

    def emit_activity_end(start_event: dict, result: object, started_at: float, *, failed: bool = False) -> None:
        if on_activity_event is not None:
            on_activity_event(
                "tool_end",
                tool_end_event(start_event, result, (time.perf_counter() - started_at) * 1000, failed=failed),
            )

    if target_device not in {"local_host", "client_browser"}:
        raise ValueError("target_device must be local_host or client_browser.")
    if POLICY.is_stopped():
        return "Hsx is paused by Emergency stop. Open Settings and choose Resume to continue.", conversation_history
    uncertain = False
    complex_task = False
    vision_image_data = ""
    vision_activity = None
    vision_activity_started = 0.0
    if resume_tool_result:
        result = resume_tool_result.get("result")
        if result is None:
            result = {"success": True, "answer": resume_tool_result["answer"]}
        conversation_history.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": resume_tool_result["tool_call_id"],
                "content": json.dumps(result, ensure_ascii=False),
            }],
        })
    else:
        MEMORY.record_message(session_id, "user", user_input)
        conversation_history.append({"role": "user", "content": user_input})

        if PREFERENCE_PROMPT.search(user_input):
            clean_fact = re.sub(
                r"^(?:always remember(?: that)?|remember that|remember:|please remember(?: that)?|note that|keep in mind that|standing rule:?)\s*",
                "",
                user_input.strip(),
                flags=re.IGNORECASE,
            ).strip()
            fact_to_save = clean_fact or user_input.strip()
            MEMORY.save_fact(session_id, fact_to_save)
            answer = f"I will remember that: {fact_to_save}."
            conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            MEMORY.record_message(session_id, "assistant", answer)
            return answer, conversation_history

        image_paths = _attached_image_paths(user_input)
        request_text = user_input.split("\n\nUploaded workspace files:", 1)[0].strip()
        if image_paths and ATTACHED_IMAGE_PROMPT.search(request_text):
            describe_image = skill_map.get("describe_image")
            if describe_image is None:
                answer = "Image description is unavailable because the describe_image tool is not configured."
                conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
                return answer, conversation_history
            call_id = f"image-{uuid.uuid4().hex}"
            activity = emit_activity_start(
                "describe_image",
                {"path": image_paths[0], "question": request_text},
                call_id,
            )
            started = time.perf_counter()
            try:
                result = describe_image(path=image_paths[0], question=request_text)
                description = str(result.get("description", "")).strip() if isinstance(result, dict) else ""
                if not description:
                    raise RuntimeError("The image tool returned no description.")
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                emit_activity_end(activity, {"success": False, "error": str(error)}, started, failed=True)
                answer = f"I couldn't describe the attached image: {error}"
            else:
                emit_activity_end(activity, result, started)
                answer = description
            conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            MEMORY.record_message(session_id, "assistant", answer)
            return answer, conversation_history

        if CAMERA_STOP_PROMPT.search(user_input):
            from agent.hardware.camera import CAMERA, camera_session_owner
            from agent.notifier import NOTIFIER
            owner = camera_session_owner(session_id)
            CAMERA.release(owner)
            active = CAMERA.is_started()
            POLICY.audit_event("camera_off", session_id, job_id, "stopped")
            camera_event = {"active": active}
            NOTIFIER.broadcast_event("camera_state", camera_event)
            if on_activity_event is not None:
                on_activity_event("camera_state", camera_event)
            answer = (
                "This chat's camera access is off."
                if not active
                else "This chat's camera access is off, but another active camera session is still using the camera."
            )
            conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            MEMORY.record_message(session_id, "assistant", answer)
            return answer, conversation_history

        if CAMERA_START_PROMPT.search(user_input) or _vision_request(user_input):
            from agent.hardware.camera import CAMERA, camera_session_owner
            from agent.notifier import NOTIFIER
            import os
            import shutil
            import threading

            if not shutil.which("ffmpeg"):
                return _finish_vision_notice(
                    "The camera cannot start because ffmpeg is not installed on this host. Please install ffmpeg to enable camera streaming.",
                    conversation_history,
                    session_id,
                )
            if not os.path.exists(CAMERA.device):
                return _finish_vision_notice(
                    f"The camera device ({CAMERA.device}) is not available or disconnected. Please ensure your HP camera is plugged in.",
                    conversation_history,
                    session_id,
                )

            camera_tool = {"name": "vision_capture"}
            decision = POLICY.decide(
                camera_tool,
                {},
                session_id,
                (approvals or {}).get("vision_capture"),
            )
            if not decision.get("allowed"):
                if decision.get("status") == "cancelled":
                    return _finish_vision_notice(
                        "I won't start the camera. No camera access was used.",
                        conversation_history,
                        session_id,
                    )
                raise ApprovalRequest(
                    "vision_capture",
                    {},
                    decision["approval_request"],
                    _serializable_history(conversation_history),
                    f"camera-{uuid.uuid4().hex}",
                )

            owner = camera_session_owner(session_id)
            had_owner = CAMERA.has_owner(owner)
            previous_frame = CAMERA.latest_frame()
            CAMERA.acquire(owner)
            frame = CAMERA.wait_for_frame(
                previous_frame.version if previous_frame else -1,
                timeout=5.0,
            )
            if frame is None:
                status = CAMERA.health_check()
                if not had_owner:
                    CAMERA.release(owner)
                reason = status.get("last_error") or "No fresh frame arrived within 5 seconds."
                return _finish_vision_notice(
                    f"The camera did not start: {reason}",
                    conversation_history,
                    session_id,
                )

            POLICY.audit_event("camera_access", session_id, job_id, "started")

            def _auto_stop_camera():
                time.sleep(300)
                if owner and CAMERA.has_owner(owner):
                    CAMERA.release(owner)
                if not CAMERA.is_started():
                    POLICY.audit_event("camera_auto_stop", session_id, job_id, "stopped")
                    NOTIFIER.broadcast_event(
                        "camera_state",
                        {"active": False},
                    )
            threading.Thread(target=_auto_stop_camera, daemon=True).start()

            camera_event = {"active": True, "device": CAMERA.device, "session_id": session_id}
            NOTIFIER.broadcast_event("camera_state", camera_event)
            if on_activity_event is not None:
                on_activity_event("camera_state", camera_event)

            is_describe = bool(_vision_request(user_input) or re.search(r"\b(?:what do you see|describe|show (?:me )?(?:the )?room)\b", user_input, re.I))
            if is_describe:
                if frame.data:
                    if on_activity_event is not None:
                        on_activity_event(
                            "camera_snapshot",
                            {"source": "/api/camera/snapshot"},
                        )
                    encoded = base64.b64encode(frame.data).decode("ascii")
                    vision_resp = call_llm(
                        [{"role": "user", "content": [
                            {"type": "text", "text": "Describe what you see in the room through the camera in 2-3 concise sentences."},
                            {"type": "image", "mime_type": "image/jpeg", "data": encoded},
                        ]}],
                        [],
                        "You are describing what you see through the HP camera.",
                        sensitive=sensitive,
                        on_provider_notice=on_provider_notice,
                        job_id=job_id,
                        prefer_cloud=True,
                        required_provider="gemini",
                        disable_panel=True,
                    )
                    description = _response_text(vision_resp)
                    answer = (
                        f"{description}\n\nA fresh camera frame was captured. "
                        "The browser camera card is connecting to the live feed."
                    )
            else:
                answer = (
                    "The camera service is capturing fresh frames. "
                    "The browser camera card is connecting; it will show Live when video arrives."
                )

            conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
            MEMORY.record_message(session_id, "assistant", answer)
            return answer, conversation_history

        visual_request = False
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
                    "Local camera capture is disabled. Set HSX_ENABLE_VISION_CAPTURE=1 in .env to enable it.",
                    conversation_history,
                    session_id,
                )
            approved, notice = _vision_approval(session_id, approvals)
            if not approved:
                return _finish_vision_notice(notice, conversation_history, session_id)
            vision_activity_started = time.perf_counter()
            vision_activity = emit_activity_start(
                "vision_capture", {}, f"vision-{uuid.uuid4().hex}"
            )
            try:
                frame_path = capture_frame()
                with frame_path.open("rb") as frame:
                    vision_image_data = base64.b64encode(frame.read()).decode("ascii")
            except (OSError, RuntimeError, ValueError) as error:
                emit_activity_end(
                    vision_activity,
                    {"success": False, "error": str(error)},
                    vision_activity_started,
                    failed=True,
                )
                raise
        if tools:
            prediction_started = time.perf_counter()
            predicted_tool, confidence = INTENT_ROUTER.predict_with_confidence(user_input)
            inference_ms = (time.perf_counter() - prediction_started) * 1000
        else:
            predicted_tool, confidence, inference_ms = "", 0.0, 0.0
        tool_names = {item.get("name") for item in tools}
        if "web_search" in tool_names and WEB_SEARCH_PROMPT.search(user_input):
            predicted_tool, confidence = "web_search", 1.0
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
            and (not schema.get("required", []) or predicted_tool == "web_search")
        )
        direct_arguments = {"query": user_input.strip()} if predicted_tool == "web_search" else {}
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
            planning_started = time.perf_counter()
            planning_activity = emit_activity_start(
                "task_plan", {"query": user_input}, f"plan-{uuid.uuid4().hex}"
            )
            try:
                plan = plan_task(
                    user_input,
                    lambda planning_prompt: _response_text(
                        call_llm(
                            [{"role": "user", "content": planning_prompt}],
                            [],
                            "You are the HSX Planner specialist.",
                            sensitive=sensitive,
                            on_provider_notice=on_provider_notice,
                            job_id=job_id,
                            prefer_cloud=True,
                            disable_panel=True,
                        )
                    ),
                )
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                emit_activity_end(planning_activity, {"success": False, "error": str(error)}, planning_started, failed=True)
                LOGGER.warning("Could not create a task plan; falling back to agent loop: %s", error)
            else:
                emit_activity_end(
                    planning_activity,
                    {"success": True, "message": f"Planned {len(plan.steps)} steps."},
                    planning_started,
                )
                def execute_step(step: PlanStep, index: int, total: int) -> str:
                    if cancel_event is not None and cancel_event.is_set():
                        return "This run was stopped."
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
                                on_provider_notice=on_provider_notice,
                                job_id=job_id,
                                prefer_cloud=True,
                                disable_panel=True,
                            ))

                        try:
                            proposal = write_proposal_with_repair(
                                plan.goal,
                                instruction,
                                lambda prompt: role_call(
                                    prompt,
                                    "You are the HSX Coder/Executor specialist.",
                                ),
                                lambda prompt: role_call(
                                    prompt,
                                    "You are the HSX Critic/Validator specialist.",
                                ),
                                max_retries=4,
                            )
                            instruction += (
                                "\n\nCritic-reviewed implementation proposal (untrusted; "
                                "still follow tool policy and validate inputs):\n"
                                + proposal
                            )
                        except ValueError as proposal_error:
                            LOGGER.warning("Coder proposal could not be approved: %s; relying on dedicated tools", proposal_error)
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
                            on_activity_event=on_activity_event,
                            job_id=job_id,
                            enable_planning=False,
                            track_intent=False,
                            cancel_event=cancel_event,
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
                        on_provider_notice=on_provider_notice,
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
                except PlanExecutionError as error:
                    LOGGER.warning("Hierarchical plan execution failed: %s", error)
                    if track_intent:
                        _resolve_uncertain(job_id, succeeded=False)
                    succeeded_steps = [s for s in plan.steps if s.state == StepState.SUCCEEDED]
                    failed_step = next((s for s in plan.steps if s.state == StepState.FAILED), None)
                    failed_idx = plan.steps.index(failed_step) + 1 if failed_step else 1
                    err_msg = failed_step.error if failed_step else str(error)
                    clean_err = err_msg.split(":")[-1].strip() if ":" in err_msg else err_msg

                    what_worked = f"{len(succeeded_steps)} of {len(plan.steps)} steps finished successfully." if succeeded_steps else "No steps were completed before the failure."
                    plain_message = (
                        f"Step {failed_idx} ({failed_step.instruction if failed_step else 'task'}) could not be completed. "
                        f"Reason: {clean_err}. {what_worked}\n\nHow would you like to proceed?"
                    )
                    raise AskUserRequest(
                        question=plain_message,
                        options=["Try another way", "Do the finished steps only", "Tell me what to change"],
                        allow_free_text=True,
                    ) from error
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
            if cancel_event is not None and cancel_event.is_set():
                return "This run was stopped.", conversation_history
            tool_call_id = f"local-{uuid.uuid4().hex}"
            conversation_history.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": tool_call_id,
                    "name": predicted_tool,
                    "input": direct_arguments,
                }],
            })
            decision = POLICY.decide(tool, direct_arguments, session_id, (approvals or {}).get(predicted_tool))
            if not decision["allowed"]:
                activity_start = emit_activity_start(predicted_tool, direct_arguments, tool_call_id)
                activity_start["icon"] = "shield"
                ROUTER_LOGGER.log_tool_prediction(
                    session_id, job_id, predicted_tool, tool_call_id
                )
                ROUTER_LOGGER.log_tool_execution(
                    session_id, job_id, predicted_tool, tool_call_id, 0, "denied"
                )
                if decision.get("status") == "waiting_approval":
                    if on_activity_event is not None:
                        on_activity_event("tool_end", {
                            **activity_start,
                            "status": "waiting",
                            "duration_ms": 0,
                            "output": "Waiting for approval.",
                        })
                    paused = {"type": "text", "text": "Paused: waiting for user approval."}
                    conversation_history[-1]["content"].append(paused)
                    raise ApprovalRequest(
                        predicted_tool,
                        direct_arguments,
                        decision["approval_request"],
                        _serializable_history(conversation_history),
                        tool_call_id,
                    )
                if on_activity_event is not None:
                    on_activity_event("tool_end", {
                        **activity_start,
                        "status": "failed",
                        "duration_ms": 0,
                        "output": "Blocked by the host safety policy.",
                    })
                answer = "That action is blocked by the host safety policy."
                conversation_history.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
                return answer, conversation_history
            ROUTER_LOGGER.log_tool_prediction(
                session_id, job_id, predicted_tool, tool_call_id
            )
            activity_start = emit_activity_start(predicted_tool, direct_arguments, tool_call_id)
            started = time.perf_counter()
            try:
                if cancel_event is not None and cancel_event.is_set():
                    return "This run was stopped.", conversation_history
                result = skill_map[predicted_tool](**direct_arguments)
            except AskUserRequest as question:
                ROUTER_LOGGER.log_tool_execution(
                    session_id,
                    job_id,
                    predicted_tool,
                    tool_call_id,
                    (time.perf_counter() - started) * 1000,
                    "needs_input",
                )
                if on_activity_event is not None:
                    on_activity_event("tool_end", {
                        **activity_start,
                        "status": "waiting",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "output": "Waiting for your answer.",
                    })
                question.checkpoint = _serializable_history(conversation_history)
                question.tool_call_id = tool_call_id
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                result = {"success": False, "error": str(error)}
                status = "failed"
            else:
                status = "succeeded"
            emit_activity_end(activity_start, result, started, failed=status == "failed")
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
            answer = _friendly_structured_answer(result)
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
        try:
            graph_context = KNOWLEDGE_GRAPH.prompt_context(user_input)
            if graph_context:
                rag_context = "\n\n".join(filter(None, (rag_context, graph_context)))
        except (OSError, RuntimeError, ValueError):
            LOGGER.exception("Could not retrieve local knowledge graph context.")
    for _ in range(max_iterations):
        if POLICY.is_stopped():
            return "Hsx is paused by Emergency stop. Open Settings and choose Resume to continue.", conversation_history
        if cancel_event is not None and cancel_event.is_set():
            return "This run was stopped.", conversation_history
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
        try:
            response = call_llm(
                request_history,
                tools,
                memory_context,
                sensitive=sensitive,
                target_device=target_device,
                body_context=body_context,
                on_text_delta=on_text_delta if not tools else None,
                on_provider_notice=on_provider_notice,
                job_id=job_id,
                prefer_cloud=uncertain,
                required_provider="gemini" if vision_image_data else "",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            if vision_activity is not None:
                emit_activity_end(
                    vision_activity,
                    {"success": False, "error": str(error)},
                    vision_activity_started,
                    failed=True,
                )
                vision_activity = None
            raise
        if vision_activity is not None:
            emit_activity_end(
                vision_activity,
                {"success": True, "path": str(frame_path)},
                vision_activity_started,
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
            answer = _friendly_model_answer(_text_from_response(response))
            if tools and on_text_delta is not None and answer:
                on_text_delta(answer)
            MEMORY.record_message(session_id, "assistant", answer)
            if cache_enabled:
                try:
                    SEMANTIC_CACHE.put(user_input, answer)
                except (OSError, RuntimeError, ValueError) as error:
                    LOGGER.warning("Could not store semantic cache response: %s", error)
            if track_intent:
                _resolve_uncertain(job_id, resolved_tool, succeeded=True)
            return answer, conversation_history
        if on_activity_event is not None:
            narration = "\n".join(
                block.text for block in blocks
                if getattr(block, "type", "") == "text" and getattr(block, "text", "").strip()
            )
            if narration:
                on_activity_event("activity_narration", {"text": narration[:1000]})
        conversation_history.append({"role": "assistant", "content": to_plain_data(blocks)})
        results = []
        for call_index, call in enumerate(tool_calls):
            if cancel_event is not None and cancel_event.is_set():
                return "This run was stopped.", conversation_history
            ROUTER_LOGGER.log_tool_prediction(session_id, job_id, call.name, call.id)
            skill = next((tool for tool in tools if tool["name"] == call.name), None)
            if skill is None:
                activity_start = emit_activity_start(call.name, dict(call.input), call.id)
                emit_activity_end(activity_start, {"success": False, "error": "Unknown skill."}, time.perf_counter(), failed=True)
                ROUTER_LOGGER.log_tool_execution(session_id, job_id, call.name, call.id, 0, "unknown_tool")
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": json.dumps({"success": False, "error": "Unknown skill."})})
                continue
            decision = POLICY.decide(
                skill,
                call.input,
                session_id,
                (approvals or {}).get(call.name),
            )
            if not decision["allowed"]:
                activity_start = emit_activity_start(call.name, dict(call.input), call.id)
                activity_start["icon"] = "shield"
                ROUTER_LOGGER.log_tool_execution(session_id, job_id, call.name, call.id, 0, "denied")
                if decision.get("status") == "waiting_approval":
                    if on_activity_event is not None:
                        on_activity_event("tool_end", {
                            **activity_start,
                            "status": "waiting",
                            "duration_ms": 0,
                            "output": "Waiting for approval.",
                        })
                    content = conversation_history[-1].get("content", [])
                    content.append({"type": "text", "text": "Paused: waiting for user approval."})
                    pending_results = list(results)
                    pending_results.extend(
                        {
                            "type": "tool_result",
                            "tool_use_id": later.id,
                            "content": json.dumps({
                                "success": False,
                                "error": "This tool call is paused until the pending approval is resolved.",
                            }),
                        }
                        for later in tool_calls[call_index + 1 :]
                    )
                    if pending_results:
                        conversation_history.append({"role": "user", "content": pending_results})
                    raise ApprovalRequest(
                        call.name,
                        dict(call.input),
                        decision["approval_request"],
                        _serializable_history(conversation_history),
                        call.id,
                    )
                if on_activity_event is not None:
                    on_activity_event("tool_end", {
                        **activity_start,
                        "status": "failed",
                        "duration_ms": 0,
                        "output": "Blocked by the host safety policy.",
                    })
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps({
                        "success": False,
                        "error": "This action is blocked by the host safety policy.",
                    }),
                })
                continue
            activity_start = emit_activity_start(call.name, dict(call.input), call.id)
            started = time.perf_counter()
            try:
                tool_input = dict(call.input)
                if call.name in {"audio_play", "camera_record", "music_play"}:
                    tool_input["target_device"] = target_device
                if call.name == "describe_image" and not _image_path_was_named(
                    user_input,
                    str(tool_input.get("path", "")),
                ):
                    raise PermissionError(
                        "Only an image named or attached in your message can be sent for description."
                    )
                result = skill_map[call.name](**tool_input)
            except AskUserRequest as question:
                if on_activity_event is not None:
                    on_activity_event("tool_end", {
                        **activity_start,
                        "status": "waiting",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "output": "Waiting for your answer.",
                    })
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
                result = {"success": False, "error": str(error)}
                if isinstance(error, PermissionError) and call.name in {"system_execute", "shell"}:
                    emit_activity_end(activity_start, result, started, failed=True)
                    _raise_permission_request(
                        call, results, tool_calls, call_index, conversation_history, error
                    )
                if call.name in {"audio_play", "camera_record", "music_play"} and device_actions is not None:
                    device_actions.append({
                        "type": call.name,
                        "target_device": target_device,
                        "success": False,
                        "error": str(error),
                    })
                emit_activity_end(activity_start, result, started, failed=True)
            else:
                ROUTER_LOGGER.log_tool_execution(
                    session_id, job_id, call.name, call.id, (time.perf_counter() - started) * 1000, "succeeded"
                )
                resolved_tool = call.name
                if isinstance(result, dict) and call.name in {"audio_play", "camera_record", "music_play"} and device_actions is not None:
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
                emit_activity_end(activity_start, result, started)
            if (
                isinstance(result, dict)
                and call.name in {"system_execute", "shell"}
                and (call.name == "shell" or call.input.get("action") == "exec")
                and _permission_denied_result(result)
            ):
                _raise_permission_request(
                    call, results, tool_calls, call_index, conversation_history
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


def _permission_denied_result(result: dict) -> bool:
    text = " ".join(
        str(result.get(key, ""))
        for key in ("error", "stderr", "stdout", "message")
    ).casefold()
    if any(marker in text for marker in ("emergency stop", "kill switch", "safety policy", "host safety")):
        return False
    return any(marker in text for marker in (
        "permission denied", "operation not permitted", "access is denied",
        "requires administrator", "administrator privileges required", "sudo required",
        "sudo: a password is required", "not in the sudoers", "requires root", "must be root",
    ))


def _raise_permission_request(
    call,
    results: list,
    tool_calls: list,
    call_index: int,
    history: list,
    cause: Exception | None = None,
) -> None:
    classification = classify_action(call.name, call.input)
    permission = {
        "request_type": "permission_request",
        "summary": "Hsx wants to retry this command with administrator permission.",
        "why": "The operating system denied the command. Approval allows one non-interactive sudo attempt; Hsx will never request or store your OS password.",
        "action_pattern": classification,
        "details": str(call.input.get("command", ""))[:500],
    }
    pending_results = list(results)
    pending_results.extend(
        {
            "type": "tool_result",
            "tool_use_id": later.id,
            "content": json.dumps({"success": False, "error": "Waiting for the pending permission decision."}),
        }
        for later in tool_calls[call_index + 1 :]
    )
    if pending_results:
        history.append({"role": "user", "content": pending_results})
    approval = ApprovalRequest(
        call.name,
        dict(call.input),
        permission,
        _serializable_history(history),
        call.id,
    )
    if cause is not None:
        raise approval from cause
    raise approval


def _local_response(user_input: str, history: list) -> str:
    lowered = user_input.lower().strip()
    if lowered in {"hi", "hello", "hey"}:
        return "Hello. I’m Hsx, your local creative workspace. What are we making?"
    if "2+2" in lowered or "2 + 2" in lowered:
        return "4"
    return "I can answer directly here or create files locally in your workspace."
