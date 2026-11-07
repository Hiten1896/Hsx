import json
import sqlite3

import pytest

from agent.feedback import FeedbackStore


def test_feedback_write_read_export_redacts_private_values(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    store.update_settings(save_feedback=True, include_full_conversation=False)
    assert store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="down",
        reason="Contains user@example.com",
        note="Token=abc123 phone +1 (555) 123-4567",
        prompt_messages=[{"role": "user", "content": "Contact user@example.com"}],
        response="Password: s3cret",
        provider_calls=[{"provider": "gemini", "model": "gemini-test"}],
    )

    record = store.export_records()[0]
    assert record["rating"] == "down"
    assert record["prompt_messages"] == [{"role": "user", "content": "Contact [REDACTED]"}]
    assert "user@example.com" not in json.dumps(record)
    assert "s3cret" not in json.dumps(record)
    assert "555" not in json.dumps(record)
    assert record["metadata"]["provider_calls"] == [{"provider": "gemini", "model": "gemini-test"}]
    assert store.settings()["count"] == 1


def test_feedback_rating_update_is_idempotent(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    values = {
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "rating": "up",
    }
    store.record(**values)
    store.record(**{**values, "rating": "down", "note": "Changed my mind"})

    assert store.settings()["count"] == 1
    assert store.export_records()[0]["rating"] == "down"
    assert store.export_records()[0]["note"] == "Changed my mind"


def test_feedback_keeps_redacted_tool_calls_but_not_tool_output_and_prunes_by_cutoff(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    assert store.settings()["include_full_conversation"] is True
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
        tool_names=["shell"],
        full_conversation=[
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "shell", "input": {"command": "ls"}}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "private file contents"},
        ],
    )
    serialized = json.dumps(store.export_records()[0])
    assert '"command": "ls"' in serialized
    assert "TOOL_RESULT_OMITTED" in serialized
    assert "private file contents" not in serialized

    with store._connect() as connection:
        connection.execute("UPDATE feedback SET created_at = ?", ("2000-01-01T00:00:00+00:00",))
    assert store.prune_before("2025-01-01T00:00:00+00:00") == 1
    assert store.settings()["count"] == 0


def test_feedback_opt_out_and_delete_all(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    store.update_settings(
        save_feedback=False,
        include_full_conversation=False,
    )
    assert not store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
    )
    store.update_settings(save_feedback=True, include_full_conversation=False)
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
    )

    assert store.delete_all() == 1
    assert store.settings()["count"] == 0


def test_feedback_export_full_conversation_requires_setting_and_file_opt_in(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    store.update_settings(save_feedback=True, include_full_conversation=False)
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
        prompt_messages=[{"role": "user", "content": "Latest prompt"}],
        full_conversation=[
            {"role": "user", "content": "Earlier prompt"},
            {"role": "assistant", "content": "Prior response", "data": "file bytes"},
        ],
    )
    assert store.export_records()[0]["prompt_messages"][0]["content"] == "Latest prompt"

    store.update_settings(
        save_feedback=True,
        include_full_conversation=True,
        include_file_contents=True,
    )
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
        prompt_messages=[{"role": "user", "content": "Latest prompt"}],
        full_conversation=[
            {"role": "user", "content": "Earlier prompt"},
            {"role": "assistant", "content": "Prior response", "data": "file bytes"},
        ],
    )
    default_export = store.export_records()[0]
    assert default_export["prompt_messages"][0]["content"] == "Earlier prompt"
    assert '"data":' not in json.dumps(default_export)
    opt_in_export = store.export_records(include_file_contents=True)[0]
    assert '"data": "file bytes"' in json.dumps(opt_in_export)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        store.export_records(since="not-a-date")


def test_feedback_redacts_structured_secret_values(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    store.update_settings(save_feedback=True, include_full_conversation=False)
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
        prompt_messages=[{
            "role": "user",
            "content": {
                "api_key": "must-not-export",
                "Authorization": "Bearer must-not-export-either",
                "safe": "ordinary text",
            },
        }],
    )

    record = store.export_records()[0]
    serialized = json.dumps(record)
    assert "must-not-export" not in serialized
    assert record["prompt_messages"][0]["content"]["api_key"] == "[REDACTED]"
    assert record["prompt_messages"][0]["content"]["Authorization"] == "[REDACTED]"


def test_feedback_export_cli_writes_redacted_jsonl_without_file_contents(tmp_path):
    from tools.export_feedback import main

    database = tmp_path / "feedback.sqlite"
    output = tmp_path / "feedback.jsonl"
    store = FeedbackStore(database)
    store.update_settings(save_feedback=True, include_full_conversation=False)
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="down",
        prompt_messages=[{
            "role": "user",
            "content": "Contact user@example.com",
            "attachment_contents": "private file contents",
        }],
        response="Phone +1 (555) 123-4567",
    )

    assert main(["--database", str(database), "--output", str(output)]) == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    exported = json.loads(lines[0])
    assert "user@example.com" not in lines[0]
    assert "555" not in lines[0]
    assert "private file contents" not in lines[0]
    assert exported["prompt_messages"][0]["content"] == "Contact [REDACTED]"


def test_feedback_store_migrates_provider_call_metadata(tmp_path):
    database = tmp_path / "feedback.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE feedback (
                id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                rating TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                tool_names TEXT NOT NULL DEFAULT '[]',
                app_version TEXT NOT NULL DEFAULT '',
                client TEXT NOT NULL,
                prompt_messages TEXT NOT NULL DEFAULT '[]',
                response TEXT NOT NULL DEFAULT '',
                full_conversation TEXT NOT NULL DEFAULT '[]',
                UNIQUE (conversation_id, message_id)
            )"""
        )

    store = FeedbackStore(database)
    store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="up",
        provider_calls=[{"provider": "groq", "model": "groq-test"}],
    )
    assert store.export_records()[0]["metadata"]["provider_calls"] == [
        {"provider": "groq", "model": "groq-test"},
    ]


def test_provider_bandit_keeps_job_specific_provenance(tmp_path):
    from agent.llm_client import ProviderBandit

    state_path = tmp_path / "provider-bandit.json"
    bandit = ProviderBandit(state_path)
    bandit.remember_job_provider("job-1", "gemini", "gemini-test")
    bandit.remember_job_provider("job-1", "groq", "groq-test")
    bandit.remember_job_tool("job-1", "read_file")
    bandit.remember_job_tool("job-1", "read_file")
    bandit.remember_job_tool("job-1", "summarize_document")

    restarted = ProviderBandit(state_path)
    assert restarted.job_provenance("job-1") == {
        "provider": "groq",
        "model": "groq-test",
        "provider_calls": [
            {"provider": "gemini", "model": "gemini-test"},
            {"provider": "groq", "model": "groq-test"},
        ],
        "tool_names": ["read_file", "summarize_document"],
    }
