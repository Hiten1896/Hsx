import json
import os
from datetime import UTC, datetime, timedelta

from agent.ask_user import AskUserJobs
from agent.conversations import ConversationStore
from agent.feedback import FeedbackStore
from agent.jobs import JobQueue
from agent.memory import MemoryManager
from agent.retention import run_retention_maintenance


def test_retention_analyzes_recent_feedback_then_prunes_old_chat_records(tmp_path):
    now = datetime(2026, 5, 20, tzinfo=UTC)
    old = (now - timedelta(days=8)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    memory_dir = tmp_path / "memory"
    data_dir.mkdir()
    logs_dir.mkdir()

    feedback = FeedbackStore(data_dir / "feedback.sqlite")
    conversations = ConversationStore(data_dir / "conversations.sqlite")
    memory = MemoryManager(data_dir / "memory.sqlite")
    ask_jobs = AskUserJobs(data_dir / "ask-jobs.sqlite")
    jobs = JobQueue(data_dir / "jobs.sqlite")
    fact = memory.add_fact("The user prefers concise answers", category="preference")
    artifact = tmp_path / "Hsx Work" / "report.pdf"
    artifact.parent.mkdir()
    artifact.write_bytes(b"generated")
    feedback.record(
        conversation_id="old",
        message_id="old",
        rating="down",
        tool_names=["shell"],
    )
    feedback.record(
        conversation_id="recent",
        message_id="recent",
        rating="up",
        tool_names=["read_content"],
    )
    with feedback._connect() as connection:
        connection.execute("UPDATE feedback SET created_at = ? WHERE conversation_id = 'old'", (old,))
        connection.execute("UPDATE feedback SET created_at = ? WHERE conversation_id = 'recent'", (recent,))
    conversations.save("old", "Old chat", {"messages": []})
    conversations.save("recent", "Recent chat", {"messages": []})
    old_ask_job = ask_jobs.create_running("old request", "old-session")
    recent_ask_job = ask_jobs.create_running("recent request", "recent-session")
    ask_jobs.complete(old_ask_job["id"], "old result")
    ask_jobs.complete(recent_ask_job["id"], "recent result")
    old_job = jobs.create([{"name": "old step"}])
    recent_job = jobs.create([{"name": "recent step"}])
    with conversations._connect() as connection:
        connection.execute("UPDATE conversations SET created = ? WHERE session_id = 'old'", (old,))
        connection.execute("UPDATE conversations SET created = ? WHERE session_id = 'recent'", (recent,))
    with ask_jobs._connect() as connection:
        connection.execute("UPDATE ask_jobs SET created = ? WHERE id = ?", (old, old_ask_job["id"]))
        connection.execute("UPDATE ask_jobs SET created = ? WHERE id = ?", (recent, recent_ask_job["id"]))
    with jobs._connect() as connection:
        connection.execute("UPDATE jobs SET created = ?, state = 'done' WHERE id = ?", (old, old_job["id"]))
        connection.execute("UPDATE jobs SET created = ?, state = 'done' WHERE id = ?", (recent, recent_job["id"]))
    memory.record_message("s", "user", "old message")
    with memory._connect() as connection:
        connection.execute("UPDATE messages SET created = ?", (old,))
    training_file = data_dir / "datasets" / "sft_dataset.jsonl"
    training_file.parent.mkdir()
    training_file.write_text(
        json.dumps({"timestamp": old, "prompt": "old"}) + "\n"
        + json.dumps({"timestamp": recent, "prompt": "recent"}) + "\n",
        encoding="utf-8",
    )
    workload_file = data_dir / "workload_history.jsonl"
    workload_file.write_text(
        json.dumps({"timestamp": (now - timedelta(days=8)).timestamp(), "kind": "job", "category": "shell"}) + "\n"
        + json.dumps({"timestamp": (now - timedelta(days=2)).timestamp(), "kind": "job", "category": "shell"}) + "\n",
        encoding="utf-8",
    )
    os.utime(workload_file, (now.timestamp(), now.timestamp()))

    removed = run_retention_maintenance(
        feedback_store=feedback,
        conversations=conversations,
        memory=memory,
        ask_jobs=ask_jobs,
        jobs=jobs,
        data_dir=data_dir,
        logs_dir=logs_dir,
        memory_dir=memory_dir,
        now=now,
    )

    summary = json.loads((memory_dir / "personalization.json").read_text(encoding="utf-8"))
    assert summary["ratings"] == {"up": 1}
    assert summary["training_examples"] == 1
    assert summary["tool_ratings"] == {"read_content": {"up": 1}}
    assert removed == {
        "feedback": 1,
        "conversations": 1,
        "memory_messages": 1,
        "ask_jobs": 1,
        "jobs": 1,
        "training_records": 1,
    }
    assert [item["metadata"]["conversation_id"] for item in feedback.export_records()] == ["recent"]
    assert [item["session_id"] for item in conversations.list()] == ["recent"]
    assert ask_jobs.get(old_ask_job["id"]) is None
    assert ask_jobs.get(recent_ask_job["id"]) is not None
    assert jobs.get(old_job["id"]) is None
    assert jobs.get(recent_job["id"]) is not None
    assert memory.get_fact(fact["id"])["text"] == "The user prefers concise answers"
    assert artifact.read_bytes() == b"generated"
    assert '"prompt": "recent"' in training_file.read_text(encoding="utf-8")
    assert '"prompt": "old"' not in training_file.read_text(encoding="utf-8")
    workload_records = workload_file.read_text(encoding="utf-8").splitlines()
    assert len(workload_records) == 1
    assert json.loads(workload_records[0])["timestamp"] == (now - timedelta(days=2)).timestamp()


def test_main_entrypoint_runs_retention_and_starts_hourly_worker(monkeypatch):
    import main

    events = []

    class ThreadStub:
        def __init__(self, *, target, name, daemon):
            events.append(("thread", target, name, daemon))

        def start(self):
            events.append(("start",))

    def run_maintenance():
        events.append(("maintenance",))

    monkeypatch.setattr(main, "_run_retention_maintenance", run_maintenance)
    monkeypatch.setattr(main, "_retention_worker", lambda: None)
    monkeypatch.setattr(main.threading, "Thread", ThreadStub)

    main._start_retention_services()

    assert events == [
        ("maintenance",),
        ("thread", main._retention_worker, "hsx-retention", True),
        ("start",),
    ]
