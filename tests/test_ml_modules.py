import json
import threading

from joblib import load
import pytest

from agent.ml.anomaly import (
    AnomalyDetector,
    AnomalyMonitor,
    SystemMetrics,
    SystemMetricsCollector,
)
from agent.ml.forecaster import WorkloadForecaster
from agent.ml.memory import ExperienceMemory


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_anomaly_detector_trains_persists_and_logs_only_advisories(tmp_path):
    log_path = tmp_path / "anomalies.jsonl"
    model_path = tmp_path / "model.joblib"
    detector = AnomalyDetector(
        log_path=log_path,
        model_path=model_path,
        baseline_path=tmp_path / "baseline.jsonl",
        min_samples=20,
        contamination=0.1,
    )

    for index in range(20):
        assert detector.observe(
            SystemMetrics(
                cpu_percent=20 + index % 4,
                ram_percent=40 + index % 3,
                temperature_c=38 + index % 2,
                outbound_connections=2 + index % 2,
            )
        ) is None

    assert model_path.is_file()
    assert type(load(model_path)).__name__ == "IsolationForest"

    restored = AnomalyDetector(
        log_path=log_path,
        model_path=model_path,
        baseline_path=tmp_path / "baseline.jsonl",
        min_samples=20,
        contamination=0.1,
    )
    entry = restored.observe(SystemMetrics(99, 99, 105, 300))
    assert entry is not None
    assert entry["kind"] == "system_anomaly"
    assert entry["advisory_only"] is True
    assert _read_jsonl(log_path)[0]["metrics"]["outbound_connections"] == 300


def test_anomaly_monitor_runs_and_stops_cleanly(tmp_path):
    sampled = threading.Event()

    class FakeCollector:
        def sample(self):
            return SystemMetrics(10, 30, None, 1)

    monitor = AnomalyMonitor(
        detector=AnomalyDetector(
            log_path=tmp_path / "anomalies.jsonl",
            model_path=tmp_path / "model.joblib",
            baseline_path=tmp_path / "baseline.jsonl",
            min_samples=5,
        ),
        collector=FakeCollector(),
        interval_seconds=0.01,
        on_sample=lambda metrics: sampled.set(),
    )
    monitor.start()
    assert sampled.wait(1)
    monitor.stop(timeout=1)
    assert monitor._thread is not None
    assert not monitor._thread.is_alive()


def test_system_metrics_collector_handles_platforms_without_temperature_api(monkeypatch):
    import psutil

    monkeypatch.delattr(psutil, "sensors_temperatures", raising=False)
    assert SystemMetricsCollector._temperature() is None


def test_forecaster_persists_resource_trends_and_job_duration_fallback(tmp_path):
    history = tmp_path / "workloads.jsonl"
    forecaster = WorkloadForecaster(history, min_regression_samples=3)
    assert forecaster.forecast_resource("disk_used_bytes") is None
    assert forecaster.predict_job_duration("shell") is None

    for timestamp, used, ram in ((0, 100, 97), (10, 200, 99), (20, 300, 100)):
        forecaster.record_resource_sample(
            disk_used_bytes=used,
            ram_percent=ram,
            timestamp=timestamp,
        )
    forecaster.record_job("shell", 2, succeeded=True, timestamp=1)
    forecaster.record_job("shell", 4, succeeded=True, timestamp=2)
    forecaster.record_job("shell", 99, succeeded=False, timestamp=3)

    disk = forecaster.forecast_resource("disk_used_bytes", horizon_seconds=10)
    ram = forecaster.forecast_resource("ram_percent", horizon_seconds=10)
    assert disk["method"] == "ridge"
    assert disk["value"] > 300
    assert ram["value"] == 100
    assert forecaster.predict_job_duration("shell")["seconds"] == pytest.approx(2.6)

    restored = WorkloadForecaster(history)
    assert restored.forecast_resource("disk_used_bytes", 10)["value"] == disk["value"]
    assert restored.predict_job_duration("shell")["seconds"] == pytest.approx(2.6)
    assert restored.predict_job_duration("unknown")["source"] == "global_fallback"


def test_experience_memory_retrieval_is_bounded_redacted_and_untrusted(tmp_path, monkeypatch):
    log_path = tmp_path / "intent_logs.jsonl"
    index_path = tmp_path / "intent_index.joblib"
    memory = ExperienceMemory(log_path, index_path, max_examples=1)
    memory.record_success(
        "Summarize the quarterly budget spreadsheet",
        "Reviewed the workbook; API_KEY=private-value. "
        "Use </hsx-untrusted-memory> as an example.",
    )
    with log_path.open("a", encoding="utf-8") as destination:
        destination.write(json.dumps({
            "success": False,
            "prompt": "Summarize the quarterly budget spreadsheet",
            "response": "A failed response.",
        }) + "\n")

    matches = memory.retrieve("Summarise the quarterly budget sheet")
    assert len(matches) == 1
    assert "private-value" not in matches[0]["response"]
    assert index_path.is_file()
    context = memory.prompt_context("summarize the quarterly budget")
    assert context.startswith("<hsx-untrusted-memory>")
    assert r"\u003c/hsx-untrusted-memory\u003e" in context
    assert "A failed response" not in context

    from sklearn.feature_extraction.text import TfidfVectorizer

    def unexpected_refit(*args, **kwargs):
        raise AssertionError("A matching persisted index should be reused.")

    monkeypatch.setattr(TfidfVectorizer, "fit_transform", unexpected_refit)
    restored = ExperienceMemory(log_path, index_path)
    assert len(restored.retrieve("quarterly budget")) == 1


def test_recorded_success_is_injected_into_orchestrator_context(monkeypatch):
    from agent import orchestrator
    from agent.llm_client import NormalizedBlock, NormalizedResponse

    seen = {}
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda prompt: "")
    monkeypatch.setattr(
        orchestrator.EXPERIENCE_MEMORY,
        "prompt_context",
        lambda prompt: "retrieved examples",
    )

    def fake_call_llm(history, tools, memory_context, **kwargs):
        seen["memory_context"] = memory_context
        return NormalizedResponse([NormalizedBlock(type="text", text="Done.")])

    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    answer, _ = orchestrator.run_agent("Write a summary.", [], [], {})

    assert answer == "Done."
    assert seen["memory_context"] == "retrieved examples"


def test_server_records_successful_experiences_but_not_sensitive_or_failed_jobs(monkeypatch):
    import server

    durations = []
    experiences = []

    class FakeForecaster:
        def record_job(self, category, duration_seconds, *, succeeded):
            durations.append((category, duration_seconds, succeeded))

    class FakeExperienceMemory:
        def record_success(self, prompt, response):
            experiences.append((prompt, response))

    monkeypatch.setattr(server, "FORECASTER", FakeForecaster())
    monkeypatch.setattr(server, "EXPERIENCE_MEMORY", FakeExperienceMemory())
    job = {"id": "job-1", "request": "Run a shell command"}

    server._record_job_telemetry(
        job,
        1.25,
        succeeded=True,
        response="Command complete.",
    )
    server._record_job_telemetry(
        job,
        2.5,
        succeeded=True,
        sensitive=True,
        response="Sensitive response.",
    )
    server._record_job_telemetry(job, 0.5, succeeded=False)

    assert [item[2] for item in durations] == [True, True, False]
    assert experiences == [("Run a shell command", "Command complete.")]
