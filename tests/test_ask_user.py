from pathlib import Path

import pytest

from agent.ask_user import AskUserJobs, AskUserRequest
from agent.llm_client import NormalizedBlock, NormalizedResponse


def test_ask_user_checkpoint_survives_store_restart(tmp_path: Path):
    database = tmp_path / "jobs.db"
    jobs = AskUserJobs(database)
    request = AskUserRequest("Which spreadsheet is the source?", ["budget.xlsx", "sales.xlsx"])
    job = jobs.create(
        "Create a report",
        "session-1",
        request,
        [{"role": "user", "content": "Create a report"}],
        "tool-call-1",
    )

    restarted = AskUserJobs(database)
    restored = restarted.get(job["id"])
    assert restored["state"] == "waiting_for_user"
    assert restored["question"] == request.question
    assert restored["checkpoint"][0]["content"] == "Create a report"
    listed = restarted.list()[0]
    assert listed["id"] == job["id"]
    assert "checkpoint" not in listed
    assert "request" not in listed

    resumed = restarted.answer(job["id"], "budget.xlsx")
    assert resumed["state"] == "running"
    assert resumed["answers"] == ["budget.xlsx"]


def test_ask_user_timeout_and_question_limit(tmp_path: Path):
    database = tmp_path / "jobs.db"
    jobs = AskUserJobs(database)
    first = jobs.create("request", "s", AskUserRequest("First?"), [], "one")
    with jobs._connect() as connection:
        connection.execute("UPDATE ask_jobs SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (first["id"],))
    assert jobs.get(first["id"])["state"] == "cancelled"
    assert "timed out" in jobs.get(first["id"])["error"].lower()

    job = jobs.create("request", "s", AskUserRequest("First?"), [], "one")
    second = AskUserRequest("Second?")
    second.tool_call_id = "two"
    jobs.pause(job["id"], second)
    third = AskUserRequest("Third?")
    third.tool_call_id = "three"
    jobs.pause(job["id"], third)
    fourth = AskUserRequest("Fourth?")
    fourth.tool_call_id = "four"
    limited = jobs.pause(job["id"], fourth)
    assert limited["state"] == "cancelled"
    assert "question limit" in limited["error"].lower()


def test_target_device_survives_job_store_restart(tmp_path: Path):
    database = tmp_path / "jobs.db"
    job = AskUserJobs(database).create_running(
        "Play a tone", "local-tty", target_device="local_host"
    )
    restored = AskUserJobs(database).get(job["id"])
    assert restored["target_device"] == "local_host"
    assert restored["device_actions"] == []
    AskUserJobs(database).complete(
        job["id"],
        "Playback ready",
        [{"type": "audio_play", "source_url": "https://example.test/tone.wav"}],
    )
    completed = AskUserJobs(database).get(job["id"])
    assert completed["device_actions"][0]["type"] == "audio_play"
    listed = AskUserJobs(database).list()[0]
    assert listed["target_device"] == "local_host"
    assert listed["device_actions"][0]["type"] == "audio_play"


def test_agent_question_checkpoint_resumes_without_replaying_tool_call(tmp_path: Path, monkeypatch):
    from agent import orchestrator
    from skills.ask_user.skill import run as ask_user

    ask_response = NormalizedResponse([
        NormalizedBlock(
            type="tool_use",
            name="ask_user",
            input={"question": "Which file should I use?"},
            id="ask-call-123",
        )
    ])
    monkeypatch.setattr(orchestrator, "call_llm", lambda *args, **kwargs: ask_response)
    history = []
    with pytest.raises(AskUserRequest) as raised:
        orchestrator.run_agent(
            "Make a report",
            history,
            [{"name": "ask_user", "risk": "safe"}],
            {"ask_user": ask_user},
        )
    assert raised.value.tool_call_id == "ask-call-123"

    store = AskUserJobs(tmp_path / "jobs.db")
    job = store.create("Make a report", "session", raised.value, raised.value.checkpoint, raised.value.tool_call_id)
    job = AskUserJobs(tmp_path / "jobs.db").answer(job["id"], "source.xlsx")
    resumed_history = job["checkpoint"]
    monkeypatch.setattr(
        orchestrator,
        "call_llm",
        lambda *args, **kwargs: NormalizedResponse([NormalizedBlock(type="text", text="Report completed.")]),
    )
    response, resumed_history = orchestrator.run_agent(
        job["request"],
        resumed_history,
        [],
        {},
        session_id=job["session_id"],
        resume_tool_result={"tool_call_id": job["tool_call_id"], "answer": job["answers"][-1]},
    )
    assert response == "Report completed."
    tool_result = resumed_history[-2]["content"][0]
    assert tool_result["tool_use_id"] == "ask-call-123"
    assert "source.xlsx" in tool_result["content"]


def test_agent_overrides_media_tool_target_with_request_context(monkeypatch):
    from agent import orchestrator

    responses = iter([
        NormalizedResponse([
            NormalizedBlock(
                type="tool_use",
                name="audio_play",
                input={"source": "tone.wav", "target_device": "client_browser"},
                id="audio-call",
            )
        ]),
        NormalizedResponse([NormalizedBlock(type="text", text="Playback started.")]),
    ])
    observed = {}

    def fake_audio(source: str, target_device: str):
        observed["source"] = source
        observed["target_device"] = target_device
        return {"success": True, "target_device": target_device}

    monkeypatch.setattr(orchestrator, "call_llm", lambda *args, **kwargs: next(responses))
    actions = []
    answer, _ = orchestrator.run_agent(
        "Play the sound on this host",
        [],
        [{"name": "audio_play", "risk": "safe"}],
        {"audio_play": fake_audio},
        target_device="local_host",
        device_actions=actions,
    )
    assert answer == "Playback started."
    assert observed == {"source": "tone.wav", "target_device": "local_host"}
    assert actions == [{
        "type": "audio_play",
        "target_device": "local_host",
        "success": True,
        "message": "",
        "path": None,
        "pid": None,
    }]
