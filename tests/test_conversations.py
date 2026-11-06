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
