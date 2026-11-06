import json

import pytest

from agent.feedback import FeedbackStore


def test_feedback_write_read_export_redacts_private_values(tmp_path):
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    assert store.record(
        conversation_id="conversation-1",
        message_id="message-1",
        rating="down",
        reason="Contains user@example.com",
        note="Token=abc123 phone +1 (555) 123-4567",
        prompt_messages=[{"role": "user", "content": "Contact user@example.com"}],
        response="Password: s3cret",
    )

    record = store.export_records()[0]
    assert record["rating"] == "down"
    assert record["prompt_messages"] == [{"role": "user", "content": "Contact [REDACTED]"}]
    assert "user@example.com" not in json.dumps(record)
    assert "s3cret" not in json.dumps(record)
    assert "555" not in json.dumps(record)
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
