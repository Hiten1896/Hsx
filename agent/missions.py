"""Durable mission ledger: lets Hsx carry a long task across turns, restarts and power cuts.

A *mission* is a goal with ordered steps, notes, and a status. It is stored in data/missions.json, so when the
server (for example on the always-on HP PC) restarts, the unfinished work is still there. The agent uses the
``mission`` tool to plan, tick off steps, record findings, and mark a mission *blocked* when it needs the owner
(a login, an approval, a decision). "Continue the Android project" then simply resumes the mission.

On startup ``announce_unfinished`` raises one notification listing missions that were left active or blocked.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from config import DATA_DIR

LOGGER = logging.getLogger("hsx.missions")
PATH = DATA_DIR / "missions.json"
STEP_STATES = {"todo", "doing", "done", "failed", "skipped"}
MISSION_STATES = {"active", "blocked", "done", "failed", "cancelled"}
MAX_STEPS = 50
MAX_NOTES = 60
MAX_KEPT = 60

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(data: dict) -> None:
    try:
        from agent.ml.storage import atomic_write_json

        atomic_write_json(PATH, data)
    except ImportError:  # pragma: no cover
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(PATH)


def _load() -> dict:
    if not PATH.is_file():
        return {"version": 1, "missions": []}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("missions.json is unreadable; starting an empty ledger.")
        return {"version": 1, "missions": []}
    if not isinstance(data, dict) or not isinstance(data.get("missions"), list):
        return {"version": 1, "missions": []}
    return data


def _find(data: dict, mission_id: str) -> dict:
    for mission in data["missions"]:
        if mission.get("id") == mission_id:
            return mission
    raise ValueError("No mission with that id. Use action list to see ids.")


def _steps_from(raw: Any) -> list[str]:
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as error:
        raise ValueError("steps_json must contain valid JSON (an array of strings).") from error
    if not isinstance(items, list) or not all(isinstance(item, str) and item.strip() for item in items):
        raise ValueError("steps_json must be an array of non-empty strings.")
    return [item.strip()[:300] for item in items]


def _brief(mission: dict) -> dict:
    steps = mission.get("steps", [])
    done = sum(1 for step in steps if step["status"] in {"done", "skipped"})
    return {"id": mission["id"], "goal": mission["goal"], "status": mission["status"],
            "progress": f"{done}/{len(steps)}", "updated": mission["updated"],
            **({"blocked_reason": mission["blocked_reason"]} if mission.get("blocked_reason") else {})}


def _full(mission: dict) -> dict:
    return {**_brief(mission), "created": mission["created"], "project_path": mission.get("project_path", ""),
            "resume_hint": mission.get("resume_hint", ""), "steps": mission["steps"], "notes": mission["notes"][-15:]}


def mission(**arguments: Any) -> dict:
    action = str(arguments.get("action", "list"))
    with _lock:
        data = _load()
        if action == "list":
            wanted = arguments.get("status")
            items = [m for m in data["missions"] if not wanted or m["status"] == wanted]
            if not wanted:
                items = [m for m in items if m["status"] in {"active", "blocked"}] or items[-5:]
            return {"success": True, "missions": [_brief(m) for m in items[-20:]]}
        if action == "create":
            goal = str(arguments.get("goal", "")).strip()
            if not goal or len(goal) > 400:
                raise ValueError("goal must be 1-400 characters.")
            steps = _steps_from(arguments.get("steps_json", "[]"))[:MAX_STEPS]
            new = {
                "id": secrets.token_hex(3), "goal": goal, "status": "active", "created": _now(), "updated": _now(),
                "project_path": str(arguments.get("project_path", ""))[:300],
                "steps": [{"n": i + 1, "text": text, "status": "todo", "result": ""} for i, text in enumerate(steps)],
                "notes": [], "blocked_reason": "", "resume_hint": "",
            }
            data["missions"].append(new)
            finished = [m for m in data["missions"] if m["status"] in {"done", "failed", "cancelled"}]
            while len(data["missions"]) > MAX_KEPT and finished:
                data["missions"].remove(finished.pop(0))
            _write(data)
            return {"success": True, **_full(new)}
        mission_id = str(arguments.get("id", ""))
        item = _find(data, mission_id)
        if action in {"status", "resume"}:
            if action == "resume":
                if item["status"] in {"done", "failed", "cancelled"}:
                    raise ValueError("That mission is already finished.")
                item.update(status="active", blocked_reason="", updated=_now())
                _write(data)
            return {"success": True, **_full(item)}
        if action == "add_steps":
            new_steps = _steps_from(arguments.get("steps_json", "[]"))
            if len(item["steps"]) + len(new_steps) > MAX_STEPS:
                raise ValueError(f"A mission can have at most {MAX_STEPS} steps.")
            for text in new_steps:
                item["steps"].append({"n": len(item["steps"]) + 1, "text": text, "status": "todo", "result": ""})
        elif action == "update_step":
            number = int(arguments.get("step", 0))
            status = str(arguments.get("status", ""))
            if status not in STEP_STATES:
                raise ValueError("status must be todo, doing, done, failed or skipped.")
            step = next((s for s in item["steps"] if s["n"] == number), None)
            if step is None:
                raise ValueError("No step with that number.")
            step["status"] = status
            if arguments.get("result"):
                step["result"] = str(arguments["result"])[:500]
        elif action == "note":
            text = str(arguments.get("text", "")).strip()
            if not text:
                raise ValueError("text is required.")
            item["notes"].append({"t": _now(), "text": text[:600]})
            item["notes"] = item["notes"][-MAX_NOTES:]
        elif action == "block":
            item["status"] = "blocked"
            item["blocked_reason"] = str(arguments.get("reason", "Waiting for the owner."))[:300]
            item["resume_hint"] = str(arguments.get("resume_hint", ""))[:400]
        elif action == "finish":
            status = str(arguments.get("status", "done"))
            if status not in {"done", "failed", "cancelled"}:
                raise ValueError("status must be done, failed or cancelled.")
            item["status"] = status
            item["blocked_reason"] = ""
            if arguments.get("summary"):
                item["notes"].append({"t": _now(), "text": "Summary: " + str(arguments["summary"])[:600]})
        else:
            raise ValueError("action must be list, create, status, resume, add_steps, update_step, note, block or finish.")
        item["updated"] = _now()
        _write(data)
        return {"success": True, **_brief(item)}


def unfinished() -> list[dict]:
    with _lock:
        return [_brief(m) for m in _load()["missions"] if m["status"] in {"active", "blocked"}]


def announce_unfinished(notify: Callable[[str, str], None]) -> int:
    items = unfinished()
    if items:
        lines = "; ".join(f"{m['goal'][:60]} ({m['status']}, {m['progress']})" for m in items[:4])
        notify(f"{len(items)} unfinished mission(s): {lines}", "Say 'continue' and name the mission to resume it.")
    return len(items)


def register_mission_tool(tools: list, skill_map: dict) -> None:
    if "mission" in skill_map or any(item.get("name") == "mission" for item in tools):
        return
    tools.append({
        "name": "mission",
        "description": (
            "Durable ledger for long, multi-step work (building a project, researching then implementing, setting up the HP PC). "
            "For any task needing more than a few tool calls: create a mission with steps, mark steps doing/done with results as you go, "
            "add notes for decisions, and use block (with reason and resume_hint) when you need the owner for a login, approval or choice. "
            "When the owner says 'continue' or asks about earlier work, call list/resume first. Survives restarts."
        ),
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "create", "status", "resume", "add_steps", "update_step", "note", "block", "finish"]},
            "id": {"type": "string"}, "goal": {"type": "string"},
            "steps_json": {"type": "string", "description": "JSON array of step strings"},
            "project_path": {"type": "string"}, "step": {"type": "integer"},
            "status": {"type": "string"}, "result": {"type": "string"}, "text": {"type": "string"},
            "reason": {"type": "string"}, "resume_hint": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["action"]},
    })
    skill_map["mission"] = mission
