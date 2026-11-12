"""Proactive suggestions: Hsx studies how it is used and proposes automations and improvements.

Two sources feed one list (data/suggestions.json):

1. Rule-based analysis of logs/audit.jsonl (free, deterministic, no model calls):
   * approval friction     - the same tool keeps waiting for approval
   * routines              - the same 3-tool sequence repeated on several days
   * habits                - a tool used around the same hour on several days
   * blocked retries       - the agent keeps attempting an action the policy denies
2. Optional model pass (HSX_SUGGESTIONS_LLM=1): proposes GitHub automations, new tools and self
   improvements from the usage summary and the list of registered tools.

Suggestions are only ever *proposals*: nothing is changed or installed until the owner accepts one and
asks Hsx to build it. The ``suggestions`` tool lists them, and new ones raise a notification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from config import BASE_DIR, DATA_DIR

LOGGER = logging.getLogger("hsx.suggestions")

STORE_PATH = DATA_DIR / "suggestions.json"
AUDIT_PATH = BASE_DIR / "logs" / "audit.jsonl"
KINDS = {"automation", "github", "improvement", "maintenance"}
STATUSES = {"new", "accepted", "dismissed", "done"}
IGNORED_TOOLS = {"ask_user", "capabilities", "delegate_task", "delegate_parallel", "suggestions", "task_plan"}
MAX_STORED = 200

_notifier: Callable[[str, str], None] | None = None


def set_notifier(callback: Callable[[str, str], None] | None) -> None:
    global _notifier
    _notifier = callback


def _key(kind: str, title: str) -> str:
    return hashlib.sha1(f"{kind}|{title.strip().lower()}".encode()).hexdigest()[:12]


def _write_json(path: Path, data: Any) -> None:
    try:
        from agent.ml.storage import atomic_write_json

        atomic_write_json(path, data)
    except ImportError:  # pragma: no cover
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


class SuggestionStore:
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = data.get("items") if isinstance(data, dict) else None
        return [item for item in items or [] if isinstance(item, dict) and "id" in item]

    def _save(self, items: list[dict]) -> None:
        _write_json(self.path, {"version": 1, "items": items[-MAX_STORED:]})

    def list(self, status: str | None = None) -> list[dict]:
        with self._lock:
            items = self._load()
        if status:
            items = [item for item in items if item.get("status") == status]
        return sorted(items, key=lambda item: (item.get("status") != "new", item.get("created", "")), reverse=False)

    def add_many(self, candidates: list[dict]) -> list[dict]:
        """Add suggestions that are not already known (even if dismissed). Returns the new ones."""
        added = []
        with self._lock:
            items = self._load()
            known = {item["id"] for item in items}
            for candidate in candidates:
                kind = candidate.get("kind", "improvement")
                title = str(candidate.get("title", "")).strip()[:120]
                if kind not in KINDS or not title:
                    continue
                sid = _key(kind, title)
                if sid in known:
                    continue
                entry = {
                    "id": sid,
                    "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "kind": kind,
                    "title": title,
                    "detail": str(candidate.get("detail", "")).strip()[:600],
                    "evidence": str(candidate.get("evidence", "")).strip()[:300],
                    "effort": candidate.get("effort") if candidate.get("effort") in {"S", "M", "L"} else "M",
                    "source": candidate.get("source", "rules"),
                    "status": "new",
                }
                items.append(entry)
                known.add(sid)
                added.append(entry)
            if added:
                self._save(items)
        return added

    def set_status(self, suggestion_id: str, status: str) -> dict | None:
        if status not in STATUSES:
            raise ValueError("status must be new, accepted, dismissed or done.")
        with self._lock:
            items = self._load()
            for item in items:
                if item["id"] == suggestion_id:
                    item["status"] = status
                    self._save(items)
                    return item
        return None


STORE = SuggestionStore()


# ----------------------------------------------------------------- rule-based analysis

def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_audit(path: Path = AUDIT_PATH, days: int = 30, now: datetime | None = None) -> list[dict]:
    if not path.is_file():
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    entries = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            when = _parse_time(entry.get("timestamp"))
            tool = entry.get("tool")
            if when is None or when < cutoff or not isinstance(tool, str) or tool in IGNORED_TOOLS:
                continue
            entry["_when"] = when
            entries.append(entry)
    entries.sort(key=lambda item: item["_when"])
    return entries


def analyze_audit(entries: list[dict]) -> list[dict]:
    found: list[dict] = []

    waiting = Counter(e["tool"] for e in entries if e.get("result_status") == "waiting_for_approval")
    for tool, count in waiting.items():
        if count >= 5:
            found.append({
                "kind": "automation",
                "title": f"Fewer approval prompts for {tool}",
                "detail": (
                    f"{tool} asked for approval {count} times recently. If these are routine, add a narrow rule "
                    "(for example allow only specific safe commands inside one project folder) so Hsx can work "
                    "without interrupting you, while risky variants still ask."
                ),
                "evidence": f"{count} approval requests in the last 30 days",
                "effort": "S",
            })

    denied = Counter(e["tool"] for e in entries if e.get("decision") in {"hard_deny", "denied"} and e.get("decision") == "hard_deny")
    for tool, count in denied.items():
        if count >= 3:
            found.append({
                "kind": "improvement",
                "title": f"Stop retrying blocked actions with {tool}",
                "detail": (
                    f"The agent attempted a blocked action with {tool} {count} times. Improve the tool description "
                    "or the planning prompt so it uses an allowed approach (for example remote_files instead of shell redirection)."
                ),
                "evidence": f"{count} hard denials",
                "effort": "M",
            })

    by_session: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        if entry.get("decision") in {"allowed", "approved"}:
            by_session[str(entry.get("session", ""))].append(entry)
    sequences: dict[tuple[str, ...], set[str]] = defaultdict(set)
    counts: Counter = Counter()
    for items in by_session.values():
        tools = [(e["tool"], e["_when"]) for e in items]
        for index in range(len(tools) - 2):
            window = tools[index:index + 3]
            names = tuple(name for name, _ in window)
            if len(set(names)) < 2:
                continue
            if (window[2][1] - window[0][1]).total_seconds() > 600:
                continue
            sequences[names].add(window[0][1].date().isoformat())
            counts[names] += 1
    for names, count in counts.items():
        if count >= 3 and len(sequences[names]) >= 2:
            found.append({
                "kind": "automation",
                "title": "Routine: " + " -> ".join(names),
                "detail": (
                    f"You (or Hsx) ran {' then '.join(names)} together {count} times on {len(sequences[names])} different days. "
                    "Hsx can turn this into one named command or a scheduled routine."
                ),
                "evidence": f"{count} repetitions on {len(sequences[names])} days",
                "effort": "S",
            })

    hours: dict[str, Counter] = defaultdict(Counter)
    days_seen: dict[tuple[str, int], set[str]] = defaultdict(set)
    for entry in entries:
        if entry.get("decision") in {"allowed", "approved"}:
            hour = entry["_when"].astimezone().hour
            hours[entry["tool"]][hour] += 1
            days_seen[(entry["tool"], hour)].add(entry["_when"].date().isoformat())
    for tool, counter in hours.items():
        hour, count = counter.most_common(1)[0]
        if len(days_seen[(tool, hour)]) >= 4:
            found.append({
                "kind": "automation",
                "title": f"Schedule {tool} around {hour:02d}:00",
                "detail": (
                    f"{tool} is used around {hour:02d}:00 on {len(days_seen[(tool, hour)])} different days. "
                    "Hsx can run it automatically at that time and post the result as a notification."
                ),
                "evidence": f"{len(days_seen[(tool, hour)])} days at hour {hour:02d}",
                "effort": "S",
            })
    return found


# ----------------------------------------------------------------- optional model pass

def _usage_summary(entries: list[dict]) -> str:
    counts = Counter(e["tool"] for e in entries if e.get("decision") in {"allowed", "approved"})
    return ", ".join(f"{tool}: {count}" for tool, count in counts.most_common(15)) or "no recorded usage yet"


def _default_ask(prompt: str) -> str:
    from agent.llm_client import call_llm

    response = call_llm(
        [{"role": "user", "content": prompt}], [], "You are the Hsx improvement analyst. Reply with JSON only.",
        sensitive=False, prefer_cloud=True, disable_panel=True,
    )
    return "\n".join(
        block.text for block in getattr(response, "content", []) if getattr(block, "type", "") == "text"
    )


def parse_llm_suggestions(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    clean = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        title = item.get("title")
        detail = item.get("detail")
        if kind in KINDS and isinstance(title, str) and isinstance(detail, str) and title.strip():
            clean.append({"kind": kind, "title": title[:120], "detail": detail[:600],
                          "effort": item.get("effort"), "evidence": "model proposal", "source": "model"})
    return clean[:5]


def generate_with_model(entries: list[dict], tool_names: list[str], ask: Callable[[str], str] | None = None) -> list[dict]:
    prompt = (
        "Hsx is a personal AI agent that controls a laptop and a Debian HP PC over SSH. Propose up to 5 concrete, "
        "high-value improvements as a JSON array of objects with keys kind (automation|github|improvement|maintenance), "
        "title (max 80 chars), detail (max 400 chars; what to build and why), effort (S|M|L).\n"
        "Prefer: GitHub automations (PR review summaries, issue triage, CI-failure watcher, release notes, dependency updates), "
        "recurring checks that could run on a schedule, missing tools, and reliability fixes. Do not repeat existing tools.\n"
        f"Registered tools: {', '.join(sorted(tool_names))[:1500]}\n"
        f"Recent usage: {_usage_summary(entries)}\n"
        "Reply with the JSON array only."
    )
    return parse_llm_suggestions((ask or _default_ask)(prompt))


# ----------------------------------------------------------------- orchestration

def refresh(*, use_model: bool | None = None, tool_names: list[str] | None = None,
            ask: Callable[[str], str] | None = None, audit_path: Path = AUDIT_PATH) -> dict:
    entries = read_audit(audit_path)
    candidates = analyze_audit(entries)
    if use_model is None:
        use_model = os.getenv("HSX_SUGGESTIONS_LLM", "0").strip().lower() in {"1", "true", "on", "yes"}
    model_error = ""
    if use_model:
        try:
            candidates += generate_with_model(entries, tool_names or [], ask)
        except Exception as error:  # a provider outage must not break the analysis
            model_error = str(error)[:200]
            LOGGER.warning("Model suggestions unavailable: %s", error)
    added = STORE.add_many(candidates)
    if added and _notifier is not None:
        try:
            first = added[0]
            extra = f" (+{len(added) - 1} more)" if len(added) > 1 else ""
            _notifier(f"Hsx has an idea: {first['title']}{extra}", first["detail"])
        except Exception:  # pragma: no cover - notifications are best effort
            LOGGER.debug("Suggestion notification failed.", exc_info=True)
    return {"success": True, "new": len(added), "analysed_events": len(entries), "model_error": model_error,
            "suggestions": [{k: item[k] for k in ("id", "kind", "title", "effort")} for item in added]}


def start_suggestion_worker(tool_names_provider: Callable[[], list[str]] | None = None, interval_hours: float = 12.0) -> threading.Thread:
    def loop() -> None:
        time.sleep(300)
        while True:
            try:
                refresh(tool_names=tool_names_provider() if tool_names_provider else None)
            except Exception:
                LOGGER.exception("Suggestion analysis failed.")
            time.sleep(interval_hours * 3600)

    thread = threading.Thread(target=loop, name="hsx-suggestions", daemon=True)
    thread.start()
    return thread


def register_suggestion_tool(tools: list, skill_map: dict) -> None:
    def suggestions(**arguments: Any) -> dict:
        action = str(arguments.get("action", "list"))
        if action == "list":
            items = [item for item in STORE.list() if item.get("status") in {"new", "accepted"}]
            return {"success": True, "suggestions": [
                {k: item.get(k) for k in ("id", "kind", "title", "detail", "effort", "status")} for item in items[:20]
            ]}
        if action == "refresh":
            return refresh(use_model=True, tool_names=[t["name"] for t in tools])
        if action in {"accept", "dismiss", "done"}:
            sid = str(arguments.get("id", ""))
            item = STORE.set_status(sid, {"accept": "accepted", "dismiss": "dismissed", "done": "done"}[action])
            if item is None:
                return {"success": False, "error": "No suggestion with that id."}
            return {"success": True, "id": sid, "status": item["status"], "title": item["title"]}
        raise ValueError("action must be list, refresh, accept, dismiss or done.")

    if "suggestions" in skill_map or any(item.get("name") == "suggestions" for item in tools):
        return
    tools.append({
        "name": "suggestions",
        "description": (
            "List, refresh or resolve Hsx's own improvement and automation suggestions (GitHub automations, routines, "
            "scheduled checks, reliability fixes). refresh asks the model for new ideas; accept/dismiss/done update one by id."
        ),
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "refresh", "accept", "dismiss", "done"]},
            "id": {"type": "string"}}, "required": ["action"]},
    })
    skill_map["suggestions"] = suggestions
