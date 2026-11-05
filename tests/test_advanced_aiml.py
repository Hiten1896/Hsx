import json

import pytest

from agent.ml.dataset_exporter import DatasetExporter
from agent.rag.index import RagIndex
from agent.swarm.roles import review_proposal, write_proposal
from agent.tools.dynamic_registry import DynamicToolRegistry


def test_rag_streams_supported_files_redacts_credentials_and_retrieves(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "guide.md").write_text(
        "The sha256 digest utility creates checksums for input text.\n",
        encoding="utf-8",
    )
    (workspace / "module.py").write_text(
        'API_KEY = "credential-value-123"\n'
        "def make_digest(text):\n"
        "    return text\n",
        encoding="utf-8",
    )
    (workspace / ".env").write_text("API_KEY=excluded-secret-987\n", encoding="utf-8")
    (workspace / "image.png").write_bytes(b"not text")
    (workspace / "data").mkdir()
    (workspace / "data" / "intent.json").write_text(
        '{"secret":"excluded-data-value"}', encoding="utf-8"
    )

    index = RagIndex(tmp_path / "rag.sqlite3", workspace, top_k=2, min_score=0.01)
    assert index.ingest() == 2
    results = index.retrieve("sha256 digest utility checksums input text")

    assert results
    assert results[0]["path"] == "guide.md"
    assert "credential-value-123" not in str(index.retrieve("make_digest API_KEY"))
    assert all(".env" not in item["path"] for item in results)
    assert "untrusted-workspace-context" in index.prompt_context("sha256 digest utility")


def test_rag_limits_retrieval_and_skips_unsupported_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for number in range(8):
        (workspace / f"note{number}.md").write_text(
            "This is a local workspace retrieval document.",
            encoding="utf-8",
        )
    index = RagIndex(tmp_path / "rag.sqlite3", workspace, top_k=2, min_score=0.01)
    index.ingest()
    assert len(index.retrieve("workspace retrieval document")) == 2
    with pytest.raises(ValueError, match="top_k"):
        index.retrieve("query", 21)


def test_dataset_exporter_redacts_secrets_and_keeps_only_safe_trajectory(tmp_path):
    path = tmp_path / "sft.jsonl"
    exporter = DatasetExporter(path)
    prompt = "Count these words; OPENAI_API_KEY=credential-value-123"
    history = [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "count_words", "input": {"text": "private argument"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "content": '{"success": false, "error": "raw error details"}',
        }]},
        {"role": "assistant", "content": [{
            "type": "tool_use", "name": "count_words", "input": {"text": "retry"},
        }]},
        {"role": "user", "content": [{
            "type": "tool_result", "content": '{"success": true, "count": 2}',
        }]},
    ]

    assert exporter.record_success(
        prompt,
        "Count complete. Authorization: Bearer another-secret-value",
        job_id="job-1",
        history=history,
    )
    assert not exporter.record_success("sensitive prompt", "sensitive answer", sensitive=True)
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert record["format"] == "sharegpt"
    assert "credential-value-123" not in json.dumps(record)
    assert "another-secret-value" not in json.dumps(record)
    assert "private argument" not in json.dumps(record)
    assert record["tool_trajectory"] == [
        {"kind": "tool_call", "tool": "count_words"},
        {"kind": "tool_result", "status": "recorded"},
        {"kind": "tool_call", "tool": "count_words"},
        {"kind": "tool_result", "status": "recorded"},
    ]
    assert record["self_healed_error_type"] == "tool_execution_error"


def test_dataset_exporter_ignores_empty_and_incomplete_examples(tmp_path):
    exporter = DatasetExporter(tmp_path / "sft.jsonl")
    assert not exporter.record_success("", "answer")
    assert not exporter.record_success("prompt", "")
    assert not exporter.path.exists()


def test_dynamic_registry_registers_only_vetted_operations_and_restores(tmp_path):
    path = tmp_path / "dynamic_tools.json"
    registry = DynamicToolRegistry(path)
    tools = []
    skill_map = {}
    spec = {
        "task": "Normalize whitespace",
        "name": "normalize_note",
        "description": "Normalize whitespace in a text value.",
        "operation": "normalize_text",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }

    result = registry.create(**spec, tools=tools, skill_map=skill_map)
    assert result["registered"] is True
    assert result["source_executed"] is False
    assert skill_map["normalize_note"](text="  tidy   text ") == {
        "success": True, "text": "tidy text",
    }

    restored_tools = []
    restored_skills = {}
    assert DynamicToolRegistry(path).restore(restored_tools, restored_skills) == 1
    assert restored_skills["normalize_note"](text="two  spaces")["text"] == "two spaces"

    with pytest.raises(ValueError, match="vetted pure-function"):
        registry.create(
            **{**spec, "name": "custom_script", "operation": "python_source"},
            tools=[],
            skill_map={},
        )


def test_swarm_coder_critic_and_local_static_checks():
    proposal = write_proposal(
        "Create a formatter",
        "Normalize whitespace.",
        lambda prompt: "```python\ndef normalize(text):\n    return ' '.join(text.split())\n```",
    )
    decision = review_proposal(
        "Create a formatter",
        "Normalize whitespace.",
        proposal,
        lambda prompt: '{"approved": true, "tests_reviewed": true, "rationale": "Scoped and tested."}',
    )
    assert decision["approved"] is True

    with pytest.raises(ValueError, match="restricted system module"):
        review_proposal(
            "Inspect files",
            "Read paths",
            "```python\nimport subprocess\n```",
            lambda prompt: '{"approved": true, "tests_reviewed": true}',
        )

    with pytest.raises(ValueError, match="did not approve"):
        review_proposal(
            "Change files",
            "Apply patch",
            "A proposal without executable code.",
            lambda prompt: '{"approved": false, "tests_reviewed": true, "rationale": "Unsafe."}',
        )


def test_orchestrator_adds_retrieved_workspace_context(monkeypatch):
    from agent import orchestrator
    from agent.llm_client import NormalizedBlock, NormalizedResponse

    seen = {}
    monkeypatch.setattr(orchestrator.RAG_INDEX, "start_ingestion", lambda: False)
    monkeypatch.setattr(
        orchestrator.RAG_INDEX,
        "prompt_context",
        lambda prompt: "<hsx-untrusted-workspace-context>local code</hsx-untrusted-workspace-context>",
    )
    monkeypatch.setattr(orchestrator.MEMORY, "prompt_context", lambda prompt: "")
    monkeypatch.setattr(orchestrator.EXPERIENCE_MEMORY, "prompt_context", lambda prompt: "")

    def fake_call_llm(history, tools, memory_context, **kwargs):
        seen["context"] = memory_context
        return NormalizedResponse([NormalizedBlock(type="text", text="Done.")])

    monkeypatch.setattr(orchestrator, "call_llm", fake_call_llm)
    answer, _ = orchestrator.run_agent("Explain the workspace module.", [], [], {})

    assert answer == "Done."
    assert "local code" in seen["context"]
