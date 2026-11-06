import cli


def test_approval_card_is_human_readable_and_contains_no_payload_json(capsys):
    cli._show_approval({
        "id": "job",
        "approval": {
            "summary": "delete 3 files in /home/hsxshare/Reports",
            "why": "This action can make an irreversible change.",
            "risk": "DANGEROUS",
            "details": "rm old-draft.txt",
        },
    })
    output = capsys.readouterr().out
    assert "HSX wants to: delete 3 files in /home/hsxshare/Reports" in output
    assert "Risk: DANGEROUS (cannot be undone)" in output
    assert "Reply: yes / no / details" in output
    assert "waiting_approval" not in output
    assert "{" not in output and "}" not in output


def test_resume_routes_next_message_to_existing_approval(monkeypatch, capsys):
    calls = []
    job = {
        "id": "job-1",
        "state": "waiting_for_approval",
        "approval_id": "approval-1",
        "approval": {
            "summary": "restart the ssh service",
            "why": "This action can make an irreversible or security-sensitive change.",
            "risk": "DANGEROUS",
            "details": "systemctl restart ssh",
        },
    }

    def request(path, payload=None):
        calls.append((path, payload))
        if path.startswith("/api/ask-jobs/pending"):
            return {"job": job}
        if path.endswith("/answer"):
            return {"job": {"id": "job-1", "state": "running"}}
        if path == "/api/ask-jobs/job-1":
            return {"job": {"id": "job-1", "state": "done", "result": "Action completed."}}
        raise AssertionError(path)

    monkeypatch.setattr(cli, "_request", request)
    monkeypatch.setattr(cli, "_request_stream", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("A new task must not be submitted while approval is pending.")
    ))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    cli._submit("I understand, proceed", session_id="tty-test")

    answer_call = next(call for call in calls if call[0].endswith("/answer"))
    assert answer_call[1] == {"answer": "I understand, proceed", "approval_id": "approval-1"}
    output = capsys.readouterr().out
    assert "Action completed." in output
    assert "waiting_approval" not in output and "{" not in output


def test_cli_sends_local_target_and_does_not_send_policy_overrides(monkeypatch, capsys):
    requests = []

    def request(path, payload=None):
        if path.startswith("/api/ask-jobs/pending"):
            return {"job": None}
        requests.append((path, payload))
        return {"job": {"id": "job-1", "state": "done", "result": "Task completed."}}

    monkeypatch.setattr(cli, "_request", request)
    monkeypatch.setattr(
        cli,
        "_request_stream",
        lambda path, payload: (
            requests.append((path, payload))
            or {"job": {"id": "job-1", "state": "running"}, "streamed_text": ""}
        ),
    )
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    cli._submit("check system information", target_device="local_host", session_id="tty-local")

    stream_payload = requests[0][1]
    assert stream_payload["target_device"] == "local_host"
    assert stream_payload["approvals"] == {}
    assert "Task completed." in capsys.readouterr().out


def test_persistent_session_id_reuses_local_id_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "SESSION_ID", "")

    first = cli._session_id()
    second = cli._session_id()

    assert first.startswith("tty-")
    assert second == first
