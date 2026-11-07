import argparse
from datetime import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

from rich.console import Console
from rich.syntax import Syntax
from rich.status import Status
from rich.text import Text

BASE_URL = os.getenv("HSX_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_ID = os.getenv("HSX_SESSION_ID", "").strip()
ACTIVITY_CONSOLE = Console(no_color=bool(os.getenv("NO_COLOR")))
ACTIVITY_STEPS: list[dict] = []
ACTIVITY_STATUSES: dict[str, Status] = {}
ACTIVITY_GLYPHS = {
    "terminal": ("›_", ">_"),
    "pencil": ("✎", "E"),
    "book": ("▤", "R"),
    "images": ("▣", "I"),
    "file-plus": ("⊕", "+"),
    "search": ("⌕", "?"),
    "globe": ("◎", "@"),
    "camera": ("◉", "C"),
    "music": ("♫", "M"),
    "download": ("↓", "v"),
    "list": ("≡", "="),
    "question": ("?", "?"),
    "shield": ("⬡", "!"),
    "clock": ("◷", "o"),
    "package": ("▣", "[]"),
    "palette": ("◍", "*"),
    "brain": ("✧", "~"),
    "save": ("▣", "S"),
    "gear": ("⚙", "*"),
}


def _activity_text(icon: str, verb: str, target: str, *, data: dict | None = None) -> Text:
    glyph, ascii_glyph = ACTIVITY_GLYPHS.get(icon, ACTIVITY_GLYPHS["gear"])
    try:
        (sys.stdout.encoding or "utf-8").encode(glyph)
    except (LookupError, UnicodeEncodeError):
        glyph = ascii_glyph
    status = (data or {}).get("status")
    style = "bold red" if status == "failed" else "bold"
    row = Text(f"{glyph} ", style="cyan")
    row.append(verb, style=style)
    row.append(" ")
    row.append(target or "request", style="cyan")
    if data and data.get("duration_ms") is not None:
        row.append(f"  {data['duration_ms'] / 1000:.1f}s", style="dim")
    diff = (data or {}).get("diff")
    if diff and not diff.get("binary"):
        row.append(f"  +{diff.get('added', 0)}", style="green")
        row.append(f" -{diff.get('removed', 0)}", style="red")
    row.overflow = "ellipsis"
    return row


def _render_activity_event(event: str, data: dict) -> None:
    if event == "activity_narration":
        ACTIVITY_CONSOLE.print(Text(str(data.get("text", "")), style="dim italic"))
        return
    call_id = str(data.get("tool_call_id", ""))
    if event == "tool_start":
        ACTIVITY_STEPS.append(dict(data))
        status = ACTIVITY_CONSOLE.status(
            _activity_text(data.get("icon", "gear"), data.get("present", "Working"), data.get("target", "")),
            spinner="dots",
        )
        status.start()
        ACTIVITY_STATUSES[call_id] = status
        return
    if event != "tool_end":
        return
    status = ACTIVITY_STATUSES.pop(call_id, None)
    if status is not None:
        status.stop()
    existing = next((index for index, step in enumerate(ACTIVITY_STEPS) if step.get("tool_call_id") == call_id), None)
    if existing is None:
        ACTIVITY_STEPS.append(dict(data))
        existing = len(ACTIVITY_STEPS) - 1
    else:
        ACTIVITY_STEPS[existing].update(data)
    ACTIVITY_CONSOLE.print(_activity_text(
        data.get("icon", "gear"),
        "Failed" if data.get("status") == "failed" else data.get("past", "Completed"),
        data.get("target", ""),
        data=data,
    ))


def _stop_activity_statuses() -> None:
    for status in ACTIVITY_STATUSES.values():
        status.stop()
    ACTIVITY_STATUSES.clear()


def _show_steps(index: int | None = None) -> None:
    if not ACTIVITY_STEPS:
        print("HSX > There are no activity steps to show.")
        return
    if index is None:
        for number, step in enumerate(ACTIVITY_STEPS, start=1):
            ACTIVITY_CONSOLE.print(_activity_text(
                step.get("icon", "gear"),
                "Failed" if step.get("status") == "failed" else step.get("past", step.get("present", "Working")),
                step.get("target", ""),
                data=step if step.get("status") != "running" else None,
            ))
        print("Use /steps N to expand a step.")
        return
    if index < 1 or index > len(ACTIVITY_STEPS):
        print(f"HSX > Step must be between 1 and {len(ACTIVITY_STEPS)}.")
        return
    step = ACTIVITY_STEPS[index - 1]
    ACTIVITY_CONSOLE.print(_activity_text(
        step.get("icon", "gear"),
        "Failed" if step.get("status") == "failed" else step.get("past", step.get("present", "Working")),
        step.get("target", ""),
        data=step,
    ))
    diff = step.get("diff")
    if isinstance(diff, dict) and diff.get("binary"):
        ACTIVITY_CONSOLE.print(f"Binary file, {max(1, round(diff.get('size_bytes', 0) / 1024))} KB")
        ACTIVITY_CONSOLE.print(f"Open: {diff.get('path', step.get('file_path', ''))}")
        return
    if isinstance(diff, dict):
        lines = []
        for hunk in diff.get("hunks", []):
            lines.append(str(hunk.get("header", "")))
            for line in hunk.get("lines", []):
                prefix = "+" if line.get("kind") == "added" else "-" if line.get("kind") == "removed" else " "
                lines.append(prefix + str(line.get("text", "")))
        ACTIVITY_CONSOLE.print(Syntax("\\n".join(lines[:200]), "diff", line_numbers=True, word_wrap=False))
        return
    details = step.get("details") or {}
    if step.get("icon") == "terminal":
        command = str(details.get("command") or step.get("target") or "")
        ACTIVITY_CONSOLE.print(Syntax(command, "bash", line_numbers=False, word_wrap=True))
        ACTIVITY_CONSOLE.print(f"Exit status: {details.get('exit_status', 'unknown')}")
        output = details.get("output_lines") or []
        ACTIVITY_CONSOLE.print("\\n".join(str(line) for line in output[:200]) or "No output.")
        if len(output) > 200:
            ACTIVITY_CONSOLE.print(f"Showing 200 of {len(output)} output lines.")
        return
    ACTIVITY_CONSOLE.print(f"Tool: {step.get('tool', 'unknown')}")
    ACTIVITY_CONSOLE.print_json(data=details.get("arguments") or {})
    ACTIVITY_CONSOLE.print(str(step.get("output") or "No additional output."))


def _session_id() -> str:
    if SESSION_ID:
        return SESSION_ID
    session_file = Path.home() / ".hsx" / "cli-session-id"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    if session_file.is_file():
        value = session_file.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"tty-[0-9a-f]{24}", value):
            return value
    value = f"tty-{uuid.uuid4().hex[:24]}"
    session_file.write_text(value, encoding="utf-8")
    try:
        os.chmod(session_file, 0o600)
    except OSError:
        pass
    return value


def _request_headers(accept: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if accept:
        headers["Accept"] = accept
    return headers


def _request(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=_request_headers(),
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HSX returned HTTP {error.code}: {detail}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not reach HSX at {BASE_URL}: {error}") from error


def _request_stream(path: str, payload: dict) -> dict:
    _stop_activity_statuses()
    ACTIVITY_STEPS.clear()
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_request_headers("text/event-stream"),
        method="POST",
    )
    event_name = ""
    job = None
    streamed_text = []
    output_started = False
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            while line := response.readline().decode("utf-8", errors="replace"):
                line = line.rstrip("\r\n")
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:].strip() or "{}")
                    if event_name == "text_delta":
                        text = data.get("text", "")
                        streamed_text.append(text)
                        if not output_started:
                            ACTIVITY_CONSOLE.print("HSX > ", end="", flush=True)
                            output_started = True
                        ACTIVITY_CONSOLE.print(text, end="", flush=True)
                    elif event_name in {"tool_start", "tool_end", "activity_narration"}:
                        _render_activity_event(event_name, data)
                    elif event_name == "job":
                        job = data
                    elif event_name == "done":
                        _stop_activity_statuses()
                        break
                elif not line:
                    event_name = ""
    except urllib.error.HTTPError as error:
        _stop_activity_statuses()
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HSX returned HTTP {error.code}: {detail}") from error
    except (OSError, json.JSONDecodeError) as error:
        _stop_activity_statuses()
        raise RuntimeError(f"Could not stream from HSX at {BASE_URL}: {error}") from error
    _stop_activity_statuses()
    ACTIVITY_CONSOLE.print()
    return {"job": job, "streamed_text": "".join(streamed_text)}


def _show_approval(job: dict) -> None:
    approval = job.get("approval") or {}
    approval_request = job.get("approval_request") or {}
    direct_status = job.get("status")
    direct_tool = job.get("tool") or job.get("tool_name")
    direct_details = job.get("details")
    raw_waiting_approval = direct_status == "waiting_approval" or approval_request.get("status") == "waiting_approval"
    if raw_waiting_approval:
        tool_name = (
            approval_request.get("tool")
            or approval.get("tool")
            or direct_tool
            or "tool"
        )
        print(f"HSX > I need your permission to run the tool '{tool_name}'. Would you like to proceed? (Yes/No)")
        details = (
            approval_request.get("details")
            or approval.get("details")
            or direct_details
        )
        if details:
            print(f"Details: {details}")
        return
    summary = approval.get("summary") or approval_request.get("summary") or "run a tool"
    why = approval.get("why") or approval_request.get("why") or "This action needs your approval."
    action_pattern = approval.get("action_pattern") or approval_request.get("action_pattern")
    details = approval.get("details") or approval_request.get("details")
    text = f"Hsx wants to: {summary}\nWhy: {why}"
    if action_pattern:
        text += f"\nPermission rule: {action_pattern}"
    if details:
        text += f"\nDetails: {details}"
    try:
        from rich.console import Console
        from rich.panel import Panel

        Console().print(Panel(text, title="Permission", border_style="yellow"))
    except ImportError:
        print(text)
    print("Reply: Allow once / Always allow this / Deny / details")


def _canonical_answer(answer: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", answer.strip().casefold()).strip()


def _approval_is_yes(answer: str) -> bool:
    normalized = _canonical_answer(answer)
    yes_values = {"yes", "y", "i understand proceed", "i understand", "proceed", "approve", "ok", "allow", "allow once", "always allow", "always allow this"}
    return normalized in yes_values or normalized.startswith("yes ") or normalized.startswith("i understand")


def _approval_is_no(answer: str) -> bool:
    normalized = _canonical_answer(answer)
    no_values = {"no", "n", "cancel", "cancelled", "stop", "deny", "not now"}
    return normalized in no_values or normalized.startswith("no ") or normalized.startswith("cancel ")


def _answer_job(job: dict, answer: str) -> dict:
    payload = {"answer": answer}
    if job.get("state") == "waiting_for_approval":
        payload["approval_id"] = job.get("approval_id", "")
    return _request(f"/api/ask-jobs/{quote(job['id'])}/answer", payload)


def _continue_existing(job: dict, answer: str) -> bool:
    if job.get("state") == "waiting_for_approval":
        _show_approval(job)
        normalized = answer.strip().casefold()
        if normalized == "details":
            details = ((job.get("approval") or {}) or (job.get("approval_request") or {})).get("details", "")
            print(f"Details: {details or 'No additional details.'}")
            return False
    response = _answer_job(job, answer)
    updated = response.get("job") or {}
    if updated.get("state") in {"waiting_for_approval", "waiting_for_user"}:
        if updated.get("error"):
            print(f"HSX > {updated['error']}")
        return False
    return True


def _submit(
    message: str,
    target_device: str = "local_host",
    approvals: dict | None = None,
    session_id: str | None = None,
) -> str | None:
    session_id = session_id or _session_id()
    pending = _request(
        f"/api/ask-jobs/pending?session_id={quote(session_id)}"
    ).get("job")
    if pending:
        if not _continue_existing(pending, message):
            return
        job_id = pending["id"]
        response = {"job": pending, "streamed_text": ""}
    else:
        response = _request_stream(
            "/api/chat/stream",
            {
                "message": message,
                "session_id": session_id,
                "target_device": target_device,
                "approvals": {},
            },
        )
        job_id = (response.get("job") or {}).get("id")
    if not job_id:
        print(response.get("response", "Request accepted."))
        return None

    while True:
        time.sleep(1)
        job = _request(f"/api/ask-jobs/{quote(job_id)}").get("job")
        if not job:
            raise RuntimeError("HSX job disappeared before completion.")
        if job["state"] == "waiting_for_user":
            print(f"\nHSX > {job['question']}")
            for index, option in enumerate(job.get("options", []), start=1):
                print(f"  {index}. {option}")
            answer = input("You > ").strip()
            if not answer:
                print("HSX > An answer is required to resume this job.")
                continue
            _request(f"/api/ask-jobs/{job['id']}/answer", {"answer": answer})
        elif job["state"] == "waiting_for_approval":
            _show_approval(job)
            answer = input("You > ").strip()
            if answer.casefold() == "details":
                details = ((job.get("approval") or {}) or (job.get("approval_request") or {})).get("details", "")
                print(f"Details: {details or 'No additional details.'}")
                continue
            _answer_job(job, answer)
        elif job["state"] == "done":
            result = job.get("result") or "Task completed."
            if not response.get("streamed_text", ""):
                print(f"HSX > {result}")
            return job_id
        elif job["state"] in {"failed", "cancelled"}:
            print(f"HSX > {job.get('error') or 'Task did not complete.'}")
            return None


def _show_activity(session_id: str | None = None) -> None:
    active_session = session_id or _session_id()
    tasks = _request(f"/api/activity?session_id={quote(active_session)}").get("tasks", [])
    print("HSX > Agent activity")
    if not tasks:
        print("No agent tasks in this session.")
        return
    for task in tasks:
        summary = str(task.get("summary") or "Agent task").replace("\n", " ")[:120]
        state = str(task.get("state") or "unknown").replace("_", " ")
        print(f"- {state}: {summary}")


def _show_artifacts() -> None:
    files = _request("/api/outputs").get("files", [])
    print("HSX > Generated files")
    if not files:
        print("No generated files yet.")
        return
    for item in files:
        print(f"- {item.get('name', 'file')} ({item.get('type', 'file')}): {item.get('path', '')}")


def _show_greeting() -> None:
    hour = datetime.now().astimezone().hour
    time_of_day = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    try:
        greeting = str(_request("/api/greeting", {"time_of_day": time_of_day}).get("greeting", "")).strip()
    except (RuntimeError, ValueError):
        greeting = ""
    if not greeting:
        greeting = "HSX is ready. What would you like to work on?"
    print(f"HSX > {greeting}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="HSX terminal chat client")
    parser.add_argument("command", nargs="?", choices={"doctor"})
    # Local targets the HSX server host; the CLI does not offer a mesh-node execution target.
    parser.add_argument("--target", choices={"local"}, default="local")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        from doctor import print_report, run_doctor

        raise SystemExit(print_report(run_doctor()))
    target_device = {"local": "local_host"}[args.target]
    last_job_id = None
    deadline = time.monotonic() + 30
    while True:
        try:
            _request("/health")
            break
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                raise SystemExit(str(error)) from error
            time.sleep(2)
    _show_greeting()
    while True:
        try:
            message = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if message.lower() in {"/quit", "/exit"}:
            return
        if not message:
            continue
        if message.lower() == "/activity":
            try:
                _show_activity()
            except (RuntimeError, ValueError) as error:
                print(f"HSX > {error}")
            continue
        if message.lower() == "/steps":
            _show_steps()
            continue
        if message.lower().startswith("/steps "):
            step_number = message[7:].strip()
            if not step_number.isdigit():
                print("HSX > Use /steps N to expand a numbered step.")
            else:
                _show_steps(int(step_number))
            continue
        if message.lower() == "/artifacts":
            try:
                _show_artifacts()
            except (RuntimeError, ValueError) as error:
                print(f"HSX > {error}")
            continue
        if message.lower() in {"/good", "/bad"}:
            if not last_job_id:
                print("HSX > There is no completed task to rate yet.")
                continue
            try:
                _request(
                    "/api/feedback",
                    {
                        "job_id": last_job_id,
                        "conversation_id": _session_id(),
                        "message_id": last_job_id,
                        "feedback": "up" if message.lower() == "/good" else "down",
                        "client": "cli",
                    },
                )
                print("HSX > Thanks for your feedback.")
            except (RuntimeError, ValueError) as error:
                print(f"HSX > {error}")
            continue
        try:
            last_job_id = _submit(message, target_device=target_device)
        except (RuntimeError, ValueError) as error:
            print(f"HSX > {error}")


if __name__ == "__main__":
    main()
