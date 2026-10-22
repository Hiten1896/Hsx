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
