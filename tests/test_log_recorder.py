import json
import logging

from agent import router
from agent.ml.dataset_exporter import DatasetExporter
from agent.ml.log_recorder import LogRecorder
from agent.router import RouterEventLogger


def test_log_recorder_batches_redacted_logs_and_structured_events(tmp_path):
    path = tmp_path / "datasets" / "agent_logs.jsonl"
    recorder = LogRecorder(path, max_bytes=16 * 1024, queue_size=32)
    assert recorder.install()
    try:
        logging.getLogger("hsx.test.recorder").warning(
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"
        )
        assert recorder.record_event(
            "tool_execution",
            {"tool": "workspace_search", "api_token": "do-not-store-this"},
        )
        assert recorder.record_event(
            "sensitive_prompt",
            {"prompt": "private text"},
            sensitive=True,
        )
        for index in range(4):
            assert recorder.record_event("trace_sample", {"details": f"{index}:" + "x" * 3900})
    finally:
        recorder.stop()

    log_files = [path, path.with_name("agent_logs.1.jsonl")]
    records = [
        json.loads(line)
        for log_file in log_files
        if log_file.exists()
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]
    combined = json.dumps(records)
    assert "abcdefghijklmnopqrstuvwxyz" not in combined
    assert "do-not-store-this" not in combined
    assert any(item.get("record_type") == "python_log" for item in records)
    assert any(
        item.get("event") == "tool_execution"
        and item["payload"]["api_token"] == "[REDACTED]"
        for item in records
    )
    assert any(
        item.get("event") == "sensitive_prompt"
        and item["payload"] == {"redacted": True}
        for item in records
    )
    assert all(log_file.stat().st_size <= recorder.max_bytes for log_file in log_files if log_file.exists())


def test_router_events_feed_central_log_and_sft_uses_dataset_directory(tmp_path, monkeypatch):
    recorder = LogRecorder(tmp_path / "events.jsonl")
    monkeypatch.setattr(router, "LOG_RECORDER", recorder)
    assert recorder.install()
    try:
        RouterEventLogger(tmp_path / "router.jsonl").log_intent_prediction(
            "session-1",
            "job-1",
            "workspace_search",
            0.94,
            4.2,
            "direct",
        )
    finally:
        recorder.stop()

    records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "router_event"
    assert records[0]["payload"]["event"] == "intent_prediction"
    assert DatasetExporter().path.name == "sft_dataset.jsonl"
    assert DatasetExporter().path.parent.name == "datasets"
