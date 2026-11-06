import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import quote

BASE_URL = os.getenv("HSX_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_ID = os.getenv("HSX_SESSION_ID", "").strip()


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
    token = os.getenv("HSX_REMOTE_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
                            print("HSX > ", end="", flush=True)
                            output_started = True
                        print(text, end="", flush=True)
                    elif event_name == "job":
                        job = data
                    elif event_name == "done":
                        break
                elif not line:
                    event_name = ""
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HSX returned HTTP {error.code}: {detail}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not stream from HSX at {BASE_URL}: {error}") from error
    print(flush=True)
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
    risk = (approval.get("risk") or approval_request.get("risk") or "UNKNOWN").upper()
    suffix = " (cannot be undone)" if risk == "DANGEROUS" else ""
    print(f"HSX wants to: {summary}")
    print(f"Why: {why}")
    print(f"Risk: {risk}{suffix}")
    print("Reply: yes / no / details")


def _canonical_answer(answer: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", answer.strip().casefold()).strip()


def _approval_is_yes(answer: str) -> bool:
    normalized = _canonical_answer(answer)
    yes_values = {"yes", "y", "i understand proceed", "i understand", "proceed", "approve", "ok"}
    return normalized in yes_values or normalized.startswith("yes ") or normalized.startswith("i understand")


def _approval_is_no(answer: str) -> bool:
    normalized = _canonical_answer(answer)
    no_values = {"no", "n", "cancel", "cancelled", "stop"}
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
) -> None:
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
        return

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
            return
        elif job["state"] in {"failed", "cancelled"}:
            print(f"HSX > {job.get('error') or 'Task did not complete.'}")
            return


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
    deadline = time.monotonic() + 30
    while True:
        try:
            _request("/health")
            break
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                raise SystemExit(str(error)) from error
            time.sleep(2)
    while True:
        try:
            message = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if message.lower() in {"/quit", "/exit"}:
            return
        if not message:
            continue
        try:
            _submit(message, target_device=target_device)
        except (RuntimeError, ValueError) as error:
            print(f"HSX > {error}")


if __name__ == "__main__":
    main()
