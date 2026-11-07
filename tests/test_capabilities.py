import json

from agent import capabilities, skill_loader
from agent.llm.router import LLMRouter
from agent.llm.types import LLMEvent, LLMRequest


def test_capability_report_uses_live_registry_and_known_gaps(monkeypatch, tmp_path):
    tools = [
        {"name": "capabilities", "description": "Report current abilities.", "input_schema": {}},
        {"name": "semantic_file_search", "description": "Search indexed files.", "input_schema": {}},
    ]
    monkeypatch.setattr(skill_loader, "_RUNTIME_TOOLS", tools)
    monkeypatch.setattr(capabilities, "_provider_health", lambda: [])
    monkeypatch.setattr(capabilities, "_camera_item", lambda: {
        "name": "Camera snapshots", "status": "needs setup", "detail": "Enable capture.",
    })
    monkeypatch.setattr(capabilities, "_audio_items", lambda: [])
    monkeypatch.setattr(capabilities.shutil, "which", lambda _name: None)
    monkeypatch.setattr(capabilities.shutil, "disk_usage", lambda _path: type(
        "DiskUsage", (), {"free": 2 * 1024**3},
    )())
    workspace = tmp_path / "workspace"
    work_dir = workspace / "Hsx Work"
    work_dir.mkdir(parents=True)
    monkeypatch.setattr(capabilities, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(capabilities, "WORK_DIR", work_dir)
    gap_file = tmp_path / "capability_gaps.json"
    gap_file.write_text(json.dumps({"known_gaps": [
        {
            "name": "Semantic file search",
            "detail": "Add the embedding index.",
            "requires_tools": ["semantic_file_search"],
        },
        {
            "name": "Document OCR",
            "detail": "Add the OCR tool.",
            "requires_tools": ["ocr_document"],
        },
    ]}), encoding="utf-8")
    monkeypatch.setattr(capabilities, "_GAPS_PATH", gap_file)

    report = capabilities.get_capabilities()

    all_items = [item for group in report["groups"] for item in group["items"]]
    assert any(item["name"] == "semantic_file_search" for item in all_items)
    assert all(item["status"] in {"works now", "needs setup", "not possible yet"} for item in all_items)
    assert [item["name"] for item in report["cannot_do"]] == ["Document OCR"]
    assert report["cannot_do"][0]["how_to_add"] == "Add the OCR tool."


def test_runtime_tool_registry_reflects_later_registration(monkeypatch):
    first = {"name": "one", "description": "One.", "input_schema": {}}
    tools = [first]
    monkeypatch.setattr(skill_loader, "_RUNTIME_TOOLS", tools)

    assert skill_loader.runtime_tool_registry() == [first]
    tools.append({"name": "two", "description": "Two.", "input_schema": {}})
    assert [tool["name"] for tool in skill_loader.runtime_tool_registry()] == ["one", "two"]


def test_personal_voice_prompt_is_used_with_fake_provider(monkeypatch, tmp_path):
    from agent import llm_client

    captured = {}

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def generate(self, request: LLMRequest):
            captured["request"] = request
            yield LLMEvent(type="text_delta", text="I am Hsx, your workspace assistant.")
            yield LLMEvent(type="stop")

    monkeypatch.setattr(llm_client, "default_router", lambda: LLMRouter([FakeProvider()]))
    monkeypatch.setattr(
        llm_client,
        "PROVIDER_BANDIT",
        llm_client.ProviderBandit(tmp_path / "bandit.json"),
    )

    response = llm_client.call_llm(
        [{"role": "user", "content": "What can you do?"}],
        [{"name": "capabilities", "description": "Live capabilities."}],
        sensitive=True,
        disable_panel=True,
    )

    prompt = captured["request"].system
    assert "Speak as Hsx" in prompt
    assert "Never say 'I'm just an AI'" in prompt
    assert "Never claim an ability is unavailable when a matching registered tool exists." in prompt
    assert "capabilities" in captured["request"].tools[0]["name"]
    assert response.content[0].text == "I am Hsx, your workspace assistant."


def test_provider_health_runs_tiny_no_tool_request(monkeypatch):
    seen = []

    class FakeProvider:
        name = "fake"
        model = "fake-model"

        def generate(self, request):
            seen.append(request)
            assert request.tools == []
            assert len(request.messages) == 1
            yield LLMEvent(type="text_delta", text="OK")

    monkeypatch.setattr("agent.llm.router.default_router", lambda: LLMRouter([FakeProvider()]))

    result = capabilities._provider_health()

    assert result == [{
        "name": "fake (fake-model)",
        "status": "works now",
        "detail": "Answered a no-tool health request.",
    }]
    assert seen
