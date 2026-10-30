import importlib.util
import json
from pathlib import Path


_RUNTIME_TOOLS: list | None = None
_RUNTIME_SKILL_MAP: dict | None = None


def _read_skill(folder: Path) -> tuple[dict, object]:
    metadata_path = folder / "skill.json"
    module_path = folder / "skill.py"
    if not metadata_path.is_file() or not module_path.is_file():
        raise ValueError("Skill folder must contain skill.json and skill.py")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    risk = metadata.get("risk", "confirm")
    if risk not in {"safe", "confirm", "dangerous"}:
        raise ValueError("risk must be safe, confirm, or dangerous")
    advisory = metadata.get("advisory", "")
    if not isinstance(advisory, str) or len(advisory) >= 300:
        raise ValueError("advisory must be a string under 300 characters")
    spec = importlib.util.spec_from_file_location(
        f"hsx_skill_{metadata['name']}", module_path
    )
    if spec is None or spec.loader is None:
        raise ImportError("could not create import spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "run", None)):
        raise TypeError("skill.py must define run(**kwargs)")
    tool = {
        "name": metadata["name"],
        "description": metadata["description"],
        "input_schema": metadata["input_schema"],
        "risk": risk,
        "advisory": advisory,
        "requires_acknowledgement": metadata.get("requires_acknowledgement", False),
    }
    return tool, module.run


def register_skill(tools: list, skill_map: dict, folder: str | Path) -> dict:
    """Validate and add or replace one skill in a live registry."""
    tool, runner = _read_skill(Path(folder).resolve())
    tools[:] = [existing for existing in tools if existing["name"] != tool["name"]]
    tools.append(tool)
    skill_map[tool["name"]] = runner
    return tool


def set_runtime_registry(tools: list, skill_map: dict) -> None:
    global _RUNTIME_TOOLS, _RUNTIME_SKILL_MAP
    _RUNTIME_TOOLS = tools
    _RUNTIME_SKILL_MAP = skill_map


def register_runtime_skill(folder: str | Path) -> dict:
    if _RUNTIME_TOOLS is None or _RUNTIME_SKILL_MAP is None:
        raise RuntimeError("The active Hsx skill registry is not configured.")
    return register_skill(_RUNTIME_TOOLS, _RUNTIME_SKILL_MAP, folder)


def load_skills(skills_dir: str = "skills") -> tuple[list, dict]:
    tools = []
    skill_map = {}
    root = Path(skills_dir)
    if not root.exists():
        return tools, skill_map

    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata_path = folder / "skill.json"
        module_path = folder / "skill.py"
        if not metadata_path.exists() or not module_path.exists():
            print(f"Warning: skipped incomplete skill folder: {folder.name}")
            continue
        try:
            tool, runner = _read_skill(folder)
            tools.append(tool)
            skill_map[tool["name"]] = runner
        except (OSError, KeyError, TypeError, ValueError, ImportError, AttributeError) as error:
            print(f"Warning: could not load {folder.name}: {error}")
    return tools, skill_map
