import importlib.util
import json
from pathlib import Path

import pytest

from agent.skill_loader import load_skills, set_runtime_registry


def _creator_module():
    path = Path(__file__).parents[1] / "skills" / "tool_creator" / "skill.py"
    spec = importlib.util.spec_from_file_location("hsx_tool_creator_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_capability_is_created_registered_and_executed(tmp_path: Path, monkeypatch):
    creator = _creator_module()
    tools = []
    skill_map = {}
    set_runtime_registry(tools, skill_map)
    monkeypatch.setattr(creator, "SCRATCH_DIR", tmp_path / "scratch")
    code = """
def run(text: str) -> dict:
    return {"success": True, "result": text.upper()}
"""

    inspection = creator.inspect_task("create a checksum badge", ["read_file", "search_files"])
    assert inspection["missing"] is True
    result = creator.run(
        task="create a checksum badge",
        name="checksum_badge",
        description="Create a deterministic uppercase checksum badge value.",
        code=code,
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )

    assert result["registered"] is True
    assert skill_map["checksum_badge"]("ready") == {"success": True, "result": "READY"}
    assert any(tool["name"] == "checksum_badge" for tool in tools)
    assert json.loads((Path(result["path"]) / "skill.json").read_text(encoding="utf-8"))["risk"] == "confirm"


def test_generated_tool_rejects_syntax_restricted_import_and_dangerous_risk(tmp_path: Path, monkeypatch):
    creator = _creator_module()
    set_runtime_registry([], {})
    monkeypatch.setattr(creator, "SCRATCH_DIR", tmp_path / "scratch")
    schema = {"type": "object", "properties": {}}
    common = {
        "task": "unsafe task",
        "name": "unsafe_tool",
        "description": "A test tool.",
        "input_schema": schema,
    }
    with pytest.raises(ValueError, match="syntax error"):
        creator.run(**common, code="def run(:\n    pass")
    with pytest.raises(ValueError, match="restricted module"):
        creator.run(**common, code="import subprocess\ndef run():\n    return {}")
    with pytest.raises(ValueError, match="risk levels"):
        creator.run(**common, code="def run():\n    return {}", risk="dangerous")


def test_registered_skill_can_be_loaded_by_the_normal_loader(tmp_path: Path):
    code = "def run():\n    return {'success': True}\n"
    folder = tmp_path / "skills" / "generated"
    folder.mkdir(parents=True)
    (folder / "skill.py").write_text(code, encoding="utf-8")
    (folder / "skill.json").write_text(json.dumps({
        "name": "generated",
        "description": "Generated test skill.",
        "risk": "safe",
        "input_schema": {"type": "object", "properties": {}},
    }), encoding="utf-8")
    tools, skill_map = load_skills(tmp_path / "skills")
    assert skill_map["generated"]() == {"success": True}
    assert tools[0]["name"] == "generated"


def test_server_bootstrap_registers_tool_creator():
    import server

    assert "tool_creator" in server.SKILL_MAP
    assert any(tool["name"] == "tool_creator" for tool in server.TOOLS)
