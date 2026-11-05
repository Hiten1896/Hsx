from __future__ import annotations

from collections.abc import Callable

from agent.ml.planner import TaskPlan, parse_plan

PLANNER_INSTRUCTIONS = (
    "You are the Planner specialist. Break the user's complex task into 1-8 ordered, "
    "concrete steps. Return JSON only with a goal string and a steps array; each step "
    "must be an object with an instruction string. Do not execute tools or claim "
    "the task is complete."
)


def plan_task(goal: str, llm_call: Callable[[str], str]) -> TaskPlan:
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("A non-empty goal is required.")
    payload = llm_call(f"{PLANNER_INSTRUCTIONS}\n\nUser task:\n{goal[:6000]}")
    return parse_plan(payload, goal)
