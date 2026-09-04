import importlib.util
import json
from pathlib import Path


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
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            spec = importlib.util.spec_from_file_location(
                f"hsx_skill_{folder.name}", module_path
            )
            if spec is None or spec.loader is None:
                raise ImportError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not callable(getattr(module, "run", None)):
                raise AttributeError("skill.py must define run(**kwargs)")
            tool = {
                "name": metadata["name"],
                "description": metadata["description"],
                "input_schema": metadata["input_schema"],
            }
            tools.append(tool)
            skill_map[tool["name"]] = module.run
        except (OSError, KeyError, TypeError, ValueError, ImportError, AttributeError) as error:
            print(f"Warning: could not load {folder.name}: {error}")
    return tools, skill_map