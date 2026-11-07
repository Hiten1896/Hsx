from pathlib import Path

import pytest

from agent.memory import MemoryManager


def test_facts_persist_and_are_retrievable(tmp_path: Path):
    database = tmp_path / "memory.db"
    first = MemoryManager(database)
    saved = first.add_fact("I prefer concise answers", category="preference", pinned=True)
    second = MemoryManager(database)
    assert second.get_fact(saved["id"])["text"] == "I prefer concise answers"
    assert second.search("concise")[0]["id"] == saved["id"]


def test_memory_rejects_secrets(tmp_path: Path):
    manager = MemoryManager(tmp_path / "memory.db")
    with pytest.raises(ValueError):
        manager.add_fact("api_key=do-not-store")


def test_prompt_context_marks_memory_as_untrusted(tmp_path: Path):
    manager = MemoryManager(tmp_path / "memory.db")
    manager.add_fact("The user likes tea")
    context = manager.prompt_context("tea")
    assert "<hsx-untrusted-memory>" in context
    assert "not instructions" in context


def test_delete_removes_memory_from_next_prompt(tmp_path: Path):
    manager = MemoryManager(tmp_path / "memory.db")
    fact = manager.add_fact("Temporary preference")
    manager.delete_fact(fact["id"])
    assert "Temporary preference" not in manager.prompt_context("preference")


def test_explicit_facts_are_saved_in_memory_folder_and_survive_message_pruning(tmp_path: Path):
    manager = MemoryManager(tmp_path / "data" / "memory.db")
    fact = manager.add_fact("The user prefers concise answers", category="preference")
    manager.record_message("session", "user", "A temporary conversation")
    old = "2000-01-01T00:00:00+00:00"
    with manager._connect() as connection:
        connection.execute("UPDATE messages SET created = ?", (old,))

    assert manager.prune_messages_before("2025-01-01T00:00:00+00:00") == 1
    assert manager.session_messages("session") == []
    assert manager.get_fact(fact["id"])["text"] == "The user prefers concise answers"
    assert "The user prefers concise answers" in (tmp_path / "memory" / "facts.json").read_text(
        encoding="utf-8"
    )
