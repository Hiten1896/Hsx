import json

from agent.ml.dpo_collector import (
    DPOCollector,
    execution_evidence,
    export_clean_pairs,
)


def _history(prompt, result, tool_name="pytest"):
    return [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": [{
            "type": "tool_use",
            "id": "tool-1",
            "name": tool_name,
        }]},
        {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "tool-1",
            "content": json.dumps(result),
        }]},
    ]


def test_dpo_requires_test_success_and_pairs_same_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.ml.dpo_collector.LOG_RECORDER.record_event", lambda *args, **kwargs: True)
    prompt = "Check parser behavior"
    collector = DPOCollector(
        tmp_path / "dpo_dataset.jsonl",
        candidates_path=tmp_path / "dpo_candidates.jsonl",
    )
    assert collector.record_execution(
        prompt,
        "All parser tests passed.",
        run_id="chosen-1",
        history=_history(prompt, {"success": True, "tests_passed": True}),
        succeeded=True,
    )["preference"] == "chosen"

    pair = collector.record_execution(
        prompt,
        "Traceback (most recent call last): parser failed.",
        run_id="rejected-1",
        history=_history(prompt, {
            "success": False,
            "tests_passed": False,
            "traceback": "Traceback (most recent call last):",
        }),
        succeeded=True,
    )

    assert pair["format"] == "dpo"
    assert pair["chosen_run_id"] == "chosen-1"
    assert pair["rejected_run_id"] == "rejected-1"
    saved = json.loads((tmp_path / "dpo_dataset.jsonl").read_text(encoding="utf-8"))
    assert saved["chosen"] == "All parser tests passed."
    assert saved["rejected"].startswith("Traceback")


def test_dpo_does_not_infer_test_success_or_store_sensitive_runs(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.ml.dpo_collector.LOG_RECORDER.record_event", lambda *args, **kwargs: True)
    prompt = "Update parser"
    assert execution_evidence([], succeeded=True)["preference"] is None
    collector = DPOCollector(tmp_path / "pairs.jsonl")
    assert collector.record_execution(
        prompt,
        "Done.",
        run_id="unknown",
        succeeded=True,
    ) is None
    assert collector.record_execution(
        prompt,
        "Done.",
        run_id="private",
        succeeded=False,
        error="failure",
        sensitive=True,
    ) is None
    assert not (tmp_path / "pairs.jsonl").exists()


def test_dpo_rejects_unhandled_exceptions_and_cleans_export(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.ml.dpo_collector.LOG_RECORDER.record_event", lambda *args, **kwargs: True)
    prompt = "Run parser checks"
    source = tmp_path / "pairs.jsonl"
    collector = DPOCollector(source)
    collector.record_execution(
        prompt,
        "Tests passed.",
        run_id="good",
        history=_history(prompt, {"success": True, "tests_passed": True}),
        succeeded=True,
    )
    pair = collector.record_execution(
        prompt,
        "",
        run_id="bad",
        succeeded=False,
        error="RuntimeError: API_KEY=private-secret",
    )
    assert pair["format"] == "dpo"
    assert "private-secret" not in source.read_text(encoding="utf-8")

    with source.open("a", encoding="utf-8") as output:
        output.write('{"format":"dpo","prompt":"","chosen":"x","rejected":"y"}\n')
        output.write(source.read_text(encoding="utf-8").splitlines()[0] + "\n")
    destination = tmp_path / "exports" / "clean.jsonl"
    assert export_clean_pairs(source, destination) == 1
    record = json.loads(destination.read_text(encoding="utf-8"))
    assert record["format"] == "dpo"
    assert record["chosen"] == "Tests passed."
