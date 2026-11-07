from datetime import UTC, datetime, timedelta

from agent.conversations import ConversationStore


def test_conversation_store_persists_and_updates_thread(tmp_path):
    path = tmp_path / "conversations.sqlite"
    store = ConversationStore(path)
    thread = {
        "messages": [{"role": "user", "text": "Tell me the machine specs"}],
        "artifacts": [{"name": "report.txt", "path": "report.txt"}],
    }
    store.save("web-session", "Tell me the machine specs", thread)

    restored = ConversationStore(path)
    assert restored.list()[0]["title"] == "Tell me the machine specs"
    assert restored.get("web-session")["thread"] == thread
    restored.rename("web-session", "Machine details")
    assert restored.list()[0]["title"] == "Machine details"
    assert restored.delete("web-session") is True
    assert restored.get("web-session") is None


def test_conversation_retention_prunes_expired_messages_but_keeps_recent_context(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite")
    now = datetime.now(UTC)
    old = (now - timedelta(days=8)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()
    store.save(
        "web-session",
        "A continuing chat",
        {"messages": [
            {"role": "user", "text": "old", "created_at": old},
            {"role": "user", "text": "recent", "created_at": recent},
        ]},
    )
    with store._connect() as connection:
        connection.execute("UPDATE conversations SET created = ? WHERE session_id = ?", (old, "web-session"))

    removed = store.prune_before((now - timedelta(days=7)).isoformat())

    assert removed == 1
    assert [message["text"] for message in store.get("web-session")["thread"]["messages"]] == ["recent"]
