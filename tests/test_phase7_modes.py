from pathlib import Path


def test_persona_prompts_exist():
    root = Path(__file__).parent.parent / "prompts"
    assert {path.stem for path in root.glob("*.md")} >= {"jarvis", "friday", "autopilot"}
