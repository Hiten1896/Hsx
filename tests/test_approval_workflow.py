import sqlite3
from datetime import timedelta

import pytest

from api.jobs import ask_job_payload
from agent.ask_user import AskUserJobs


def _pending(jobs):
    return jobs.create_approval(
        "create a report",
        "session",
        {
            "tool": "system_execute",
            "summary": "run rm old-draft.txt",
            "why": "This action can make an irreversible change.",
            "details": "rm old-draft.txt",
            "arguments": {"action": "exec", "command": "rm old-draft.txt"},
        },
        [{"role": "user", "content": "create a report"}],
        "call-1",
        target_device="local_host",
    )


def test_approval_survives_restart_and_requires_matching_id(tmp_path):
    database = tmp_path / "jobs.db"
    first = AskUserJobs(database)
    job = _pending(first)

    restarted = AskUserJobs(database)
    persisted = restarted.get(job["id"])
    assert persisted["state"] == "waiting_for_approval"
    assert persisted["approval_id"] == job["approval_id"]
    assert persisted["checkpoint"][0]["role"] == "user"
    with pytest.raises(ValueError, match="no longer pending"):
        restarted.answer_approval(job["id"], "wrong-id", "yes")

    public = ask_job_payload(persisted)
    assert public["approval"]["summary"] == "run rm old-draft.txt"
    assert "arguments" not in public["approval"]
    assert "approval_request" not in public
    assert "waiting_approval" not in str(public)


def test_approval_unknown_answer_reminds_and_keeps_pending(tmp_path):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    job = _pending(jobs)

    updated, decision = jobs.answer_approval(job["id"], job["approval_id"], "details please")
    assert decision is None
    assert updated["state"] == "waiting_for_approval"
    assert updated["error"] == "Choose Allow once or Not now for: run rm old-draft.txt"


def test_approval_yes_and_no_resolve_once(tmp_path):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    yes_job = _pending(jobs)
    updated, decision = jobs.answer_approval(yes_job["id"], yes_job["approval_id"], "proceed")
    assert decision is True
    assert updated["state"] == "running"

    no_job = _pending(jobs)
    updated, decision = jobs.answer_approval(no_job["id"], no_job["approval_id"], "cancel")
    assert decision is False
    assert updated["state"] == "running"
    assert jobs.get(no_job["id"])["answers"] == ["cancel"]


def test_always_choice_does_not_grant_persistent_approval(tmp_path):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    job = _pending(jobs)

    updated, decision = jobs.answer_approval(job["id"], job["approval_id"], "always_allow")

    assert decision is None
    assert updated["state"] == "waiting_for_approval"
    assert "Choose Allow once or Not now" in updated["error"]
    assert "grant_scope" not in updated["approval"]


def test_permission_request_state_is_public_and_uses_the_approval_id(tmp_path):
    jobs = AskUserJobs(tmp_path / "jobs.db")
    job = _pending(jobs)
    with sqlite3.connect(jobs.database_path) as connection:
        connection.execute("UPDATE ask_jobs SET state = 'running' WHERE id = ?", (job["id"],))

    permission_job = jobs.pause_approval(
        job["id"],
        {
            "request_type": "permission_request",
            "summary": "Retry with elevated permission?",
            "why": "The OS denied the command.",
            "details": "cat /root/private-file",
            "tool": "system_execute",
            "arguments": {"action": "exec", "command": "cat /root/private-file"},
        },
        job["checkpoint"],
        job["tool_call_id"],
        state="permission_request",
    )

    public = ask_job_payload(permission_job)
    assert public["state"] == "permission_request"
    assert public["approval"]["request_type"] == "permission_request"
    assert "arguments" not in public["approval"]
    resumed, approved = jobs.answer_approval(permission_job["id"], permission_job["approval_id"], "accept_once")
    assert approved is True
    assert resumed["state"] == "running"


def test_pending_approval_expires_with_clear_message(tmp_path):
    database = tmp_path / "jobs.db"
    jobs = AskUserJobs(database)
    job = _pending(jobs)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE ask_jobs SET expires_at = ? WHERE id = ?",
            ((jobs._now() - timedelta(seconds=1)).isoformat(), job["id"]),
        )

    assert jobs.get(job["id"])["state"] == "cancelled"
    assert jobs.get(job["id"])["error"] == "Approval expired; the requested action was not run."
