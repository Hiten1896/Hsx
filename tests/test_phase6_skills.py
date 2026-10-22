from pathlib import Path

from agent.skill_loader import load_skills


def test_phase6_safe_skills_are_discovered():
    tools, skill_map = load_skills(str(Path(__file__).parent.parent / "skills"))
    names = {tool["name"] for tool in tools}
    assert {"read_file", "list_files", "search_files", "notes", "system_status", "web_fetch", "web_search", "shell", "git_ops"} <= names
    assert all(callable(skill_map[name]) for name in names)
