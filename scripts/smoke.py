"""Run basic checks against a live Hsx server: python scripts/smoke.py [base_url]."""

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
SESSION = f"smoke-{time.time_ns()}"
ROOT = Path(__file__).resolve().parents[1]


def request(path, payload=None, timeout=70):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read()


def stream(message, session=SESSION):
    status, body = request("/api/chat/stream", {
        "message": message, "session_id": session, "target_device": "local_host",
    })
    return status, body.decode(errors="replace")


def run_id_from(body):
    lines = body.splitlines()
    for index, line in enumerate(lines[:-1]):
        if line == "event: job" and lines[index + 1].startswith("data: "):
            return json.loads(lines[index + 1][6:]).get("id", "")
    return ""


def tool_history_exists(job_id):
    log_path = ROOT / "logs" / "router.jsonl"
    if not job_id or not log_path.is_file():
        return False
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return any(row.get("job_id") == job_id and row.get("event") in {"tool_predicted", "tool_execution"}
               for row in rows)


def assert_stream_success(body):
    if "event: done" not in body or "event: error" in body:
        raise AssertionError("chat stream did not complete successfully")


def check(name, operation):
    try:
        operation()
    except Exception as error:  # keep reporting subsequent checks
        print(f"FAIL {name}: {error}")
        return False
    print(f"PASS {name}")
    return True


def main():
    results = []
    results.append(check("health", lambda: (
        request("/health")[0] == 200 or (_ for _ in ()).throw(AssertionError("health not OK"))
    )))
    results.append(check("hello", lambda: assert_stream_success(stream("hello")[1])))
    def ask_specs():
        status, body = stream("What are the specifications of this machine?", SESSION + "-specs")
        if status != 200:
            raise AssertionError("specs request was rejected")
        assert_stream_success(body)
        if not tool_history_exists(run_id_from(body)):
            raise AssertionError("specs response did not complete from a machine-info tool")

    results.append(check("specs question", ask_specs))

    filename = f"hsx-smoke-{time.time_ns()}.txt"
    token = f"smoke-content-{time.time_ns()}"
    path = f"Hsx Work/{filename}"
    create_prompt = f'Create a file at "{path}" containing exactly this text: {token}'
    def create_file():
        status, body = stream(create_prompt, SESSION + "-file")
        if status != 200:
            raise AssertionError("create request was rejected")
        assert_stream_success(body)
        if filename not in body:
            raise AssertionError("create task did not finish with the requested file")

    results.append(check("create a file in Hsx Work", create_file))

    def read_file():
        query = urllib.parse.urlencode({"path": path})
        status, body = request(f"/api/files/preview?{query}")
        if status != 200 or token not in json.loads(body).get("content", ""):
            raise AssertionError("file contents did not match")

    results.append(check("read the file back", read_file))

    run_state = {}

    def stop_then_resume_chat():
        response = urllib.request.urlopen(
            urllib.request.Request(
                BASE + "/api/chat/stream",
                data=json.dumps({
                    "message": "What is the CPU model and current hostname?",
                    "session_id": SESSION + "-stop", "target_device": "local_host",
                }).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=15,
        )
        run_id = ""
        while True:
            line = response.readline()
            if not line:
                break
            if line.startswith(b"event: job"):
                data_line = response.readline()
                run_id = str(json.loads(data_line.removeprefix(b"data: "))["id"])
                break
        if not run_id:
            raise AssertionError("stream did not provide a run id")
        run_state["id"] = run_id
        time.sleep(0.1)
        _, stop_body = request("/api/stop", {"run_id": run_id})
        if not json.loads(stop_body).get("stopped"):
            raise AssertionError("server did not accept cancellation for that run")
        response.close()
        status, body = stream("hello after stop", SESSION + "-stop")
        if status != 200:
            raise AssertionError("new message was rejected")
        assert_stream_success(body)

    results.append(check("stop a run then send a new message successfully", stop_then_resume_chat))

    def check_tool_history():
        run_id = run_state.get("id")
        if not tool_history_exists(run_id):
            raise AssertionError("no tool-call history was recorded for the stopped run")

    results.append(check("tool-call history after a stop", check_tool_history))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
