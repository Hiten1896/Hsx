from contextlib import nullcontext


def test_failed_job_turn_does_not_mutate_saved_session_history(monkeypatch):
    import server

    saved_history = [{"role": "assistant", "content": [{"type": "text", "text": "previous"}]}]
    monkeypatch.setitem(server.SESSION_HISTORIES, "failed-turn", saved_history)

    class Jobs:
        def get(self, _job_id):
            return {
                "id": "job",
                "state": "running",
                "session_id": "failed-turn",
                "request": "new prompt",
                "task_dir": "",
                "sensitive": False,
                "target_device": "local_host",
            }

    def fail_after_mutating(_user_input, history, *_args, **_kwargs):
        history.append({"role": "assistant", "content": [{"type": "tool_use", "id": "incomplete"}]})
        raise RuntimeError("provider failed")

    monkeypatch.setattr(server, "ASK_JOBS", Jobs())
    monkeypatch.setattr(server, "run_agent", fail_after_mutating)
    monkeypatch.setattr(server, "task_output_scope", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(server.POLICY, "audit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_fail_job", lambda *_args, **_kwargs: None)

    server._run_chat_job("job")

    assert server.SESSION_HISTORIES["failed-turn"] == saved_history
