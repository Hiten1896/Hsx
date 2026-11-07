from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable

from agent.ask_user import AskUserRequest
from config import DATA_DIR
from .storage import atomic_write_json

LOGGER = logging.getLogger("hsx.ml.planner")
MAX_STEPS = 8
MAX_STEP_CHARS = 1000


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class PlanStep:
    instruction: str
    state: StepState = StepState.PENDING
    attempts: int = 0
    result: str = ""
    error: str = ""


@dataclass
class TaskPlan:
    goal: str
    steps: list[PlanStep]
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class PlanExecutionError(RuntimeError):
    pass


def is_complex_task(prompt: str) -> bool:
    text = prompt.strip().lower()
    if len(text) < 40:
        return False

    # Never plan preferences, conversation, questions, or camera controls
    if any(text.startswith(prefix) for prefix in [
        "always remember", "remember that", "remember:", "please remember",
        "note that", "keep in mind", "don't forget", "dont forget", "standing rule",
    ]):
        return False

    if any(text.startswith(greeting) for greeting in [
        "hi", "hello", "hey", "how are you", "who are you", "thanks", "thank you",
        "good morning", "good evening", "good afternoon",
    ]):
        return False

    if any(keyword in text for keyword in [
        "turn on the camera", "show me the room", "turn off the camera", "what do you see",
        "camera on", "camera off",
    ]):
        return False

    # Only multi-step file or document jobs use the planner
    document_keywords = [
        "file", "document", "pdf", "docx", "pptx", "csv", "markdown",
        "report", "spreadsheet", "website", "html", "archive", "zip",
    ]
    has_doc_intent = any(kw in text for kw in document_keywords)
    has_numbered_steps = bool(re.search(r"(?m)^\s*(?:\d+[.)]|[-*])\s+\S", text))
    has_sequence_words = len(re.findall(r"\b(?:then|after that|next|finally|first|second|third)\b", text)) >= 2

    return has_doc_intent and (has_numbered_steps or has_sequence_words)


def parse_plan(payload: str, goal: str) -> TaskPlan:
    text = payload.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Planner did not return valid JSON: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise ValueError("A task plan must be a JSON object with a steps list.")
    instructions = []
    for step in data["steps"]:
        instruction = step.get("instruction") if isinstance(step, dict) else step
        if not isinstance(instruction, str):
            raise ValueError("Each plan step must be a string or have an instruction field.")
        instruction = instruction.strip()
        if instruction:
            instructions.append(instruction[:MAX_STEP_CHARS])
    if not 1 <= len(instructions) <= MAX_STEPS:
        raise ValueError(f"A plan must contain between 1 and {MAX_STEPS} steps.")
    plan_goal = data.get("goal", goal)
    if not isinstance(plan_goal, str):
        raise ValueError("Plan goal must be a string.")
    return TaskPlan(plan_goal[:MAX_STEP_CHARS], [PlanStep(item) for item in instructions])


class HierarchicalPlanner:
    def __init__(self, state_dir: Path | None = None, max_retries: int = 1):
        if max_retries < 0 or max_retries > 3:
            raise ValueError("max_retries must be between 0 and 3.")
        self.state_dir = state_dir or DATA_DIR / "task_plans"
        self.max_retries = max_retries
        self._lock = threading.RLock()

    def generate(
        self,
        goal: str,
        llm_call: Callable[[str], str],
    ) -> TaskPlan:
        payload = llm_call(goal)
        return parse_plan(payload, goal)

    def execute(
        self,
        plan: TaskPlan,
        step_executor: Callable[[PlanStep, int, int], str],
        reflector: Callable[[PlanStep, str], str],
        *,
        sensitive: bool = False,
    ) -> list[str]:
        results = []
        for index, step in enumerate(plan.steps, start=1):
            step.state = StepState.RUNNING
            self._save(plan, sensitive)
            while step.attempts <= self.max_retries:
                step.attempts += 1
                try:
                    result = step_executor(step, index, len(plan.steps))
                    step.result = str(result)[:MAX_STEP_CHARS]
                    step.error = ""
                    step.state = StepState.SUCCEEDED
                    results.append(step.result)
                    self._save(plan, sensitive)
                    break
                except AskUserRequest:
                    step.state = StepState.WAITING_FOR_USER
                    self._save(plan, sensitive)
                    raise
                except Exception as error:
                    step.error = str(error)[:MAX_STEP_CHARS]
                    if step.attempts > self.max_retries:
                        step.state = StepState.FAILED
                        self._save(plan, sensitive)
                        raise PlanExecutionError(
                            f"Plan step {index} failed after {step.attempts} attempt(s): {step.error}"
                        ) from error
                    try:
                        revised = reflector(step, step.error)
                    except Exception:
                        LOGGER.exception("Planner reflection failed; retrying the original step.")
                        revised = ""
                    if not isinstance(revised, str) or not revised.strip():
                        LOGGER.warning("Planner reflection returned no revised instruction.")
                    else:
                        step.instruction = revised.strip()[:MAX_STEP_CHARS]
                    self._save(plan, sensitive)
        return results

    def _save(self, plan: TaskPlan, sensitive: bool) -> None:
        if sensitive:
            return
        if not re.fullmatch(r"[a-f0-9]{32}", plan.plan_id):
            raise ValueError("Plan identifier must be a UUID hex string.")
        snapshot = {
            "version": 1,
            "plan_id": plan.plan_id,
            "goal": plan.goal,
            "steps": [
                {
                    "instruction": step.instruction,
                    "state": step.state.value,
                    "attempts": step.attempts,
                    "result": step.result,
                    "error": step.error,
                }
                for step in plan.steps
            ],
        }
        with self._lock:
            atomic_write_json(self.state_dir / f"{plan.plan_id}.json", snapshot)


TASK_PLANNER = HierarchicalPlanner()
