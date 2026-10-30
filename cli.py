import getpass
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent
BASE_URL = os.getenv("HSX_URL", "http://127.0.0.1:8000").rstrip("/")
SESSION_ID = f"tty-{uuid.uuid4().hex[:12]}"


def _token() -> str:
    token = os.getenv("HSX_AUTH_TOKEN", "").strip()
    if not token:
        token = str(dotenv_values(ROOT / ".env").get("HSX_AUTH_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(f"No HSX_AUTH_TOKEN found in {ROOT / '.env'} or environment.")
    return token


def _request(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
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
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
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
    if output_started:
        print()
    return {"job": job, "streamed_text": "".join(streamed_text)}


def _submit(message: str) -> None:
    approvals = {}
    if re.search(r"\b(camera|webcam|snapshot|photo|record|play|audio|music)\b", message, re.IGNORECASE):
        pin = getpass.getpass("Owner PIN for requested local media actions (Enter to skip): ").strip()
        if pin:
            approvals.update({"audio_play": pin, "camera_record": pin})
    response = _request_stream(
        "/api/chat/stream",
        {
            "message": message,
            "session_id": SESSION_ID,
            "target_device": "local_host",
            "approvals": approvals,
        },
    )
    job = response.get("job")
    if not job:
        print(f"HSX > {response.get('response', 'Request accepted.')}")
        return
    while True:
        time.sleep(1)
        job = _request(f"/api/ask-jobs/{job['id']}").get("job")
        if not job:
            raise RuntimeError("HSX job disappeared before completion.")
        if job["state"] == "waiting_for_user":
            print(f"HSX > {job['question']}")
            for index, option in enumerate(job.get("options", []), start=1):
                print(f"  {index}. {option}")
            answer = input("You > ").strip()
            if not answer:
                print("HSX > An answer is required to resume this job.")
                continue
            _request(f"/api/ask-jobs/{job['id']}/answer", {"answer": answer})
        elif job["state"] == "done":
            result = job.get("result") or "Task completed."
            if not response.get("streamed_text", ""):
                print(f"HSX > {result}")
            return
        elif job["state"] in {"failed", "cancelled"}:
            print(f"HSX > {job.get('error') or 'Task did not complete.'}")
            return


def main() -> None:
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
            _submit(message)
        except (RuntimeError, ValueError) as error:
            print(f"HSX > {error}")


if __name__ == "__main__":
    main()
