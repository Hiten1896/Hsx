import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from config import SCRATCH_DIR


PROTECTED_PREFIXES = (
    ".git/",
    ".github/",
    "agent/",
    "config.py",
    "server.py",
    "main.py",
    "requirements",
    "pyproject.toml",
)


@dataclass(frozen=True)
class ImprovementProposal:
    name: str
    rationale: str
    files: dict[str, str]
    tests: dict[str, str]

    @classmethod
    def from_dict(cls, payload: dict) -> "ImprovementProposal":
        proposal = cls(
            name=str(payload.get("name", "")).strip(),
            rationale=str(payload.get("rationale", "")).strip(),
            files=payload.get("files", {}),
            tests=payload.get("tests", {}),
        )
        proposal.validate()
        return proposal

    def validate(self) -> None:
        if not self.name or len(self.name) > 120:
            raise ValueError("Proposal name is required and must be at most 120 characters.")
        if not self.rationale or len(self.rationale) > 1000:
            raise ValueError("Proposal rationale is required and must be at most 1000 characters.")
        if not isinstance(self.files, dict) or not isinstance(self.tests, dict):
            raise ValueError("Proposal files and tests must be objects.")
        if not self.tests:
            raise ValueError("Every proposal must include at least one test.")
        if len(self.files) + len(self.tests) > 20:
            raise ValueError("A proposal may contain at most 20 files.")
        for relative_path in [*self.files, *self.tests]:
            self._validate_path(relative_path)
        if any(path.lower().startswith(("requirements", "pyproject.toml")) for path in self.files):
            raise ValueError("Proposals cannot change dependencies or package configuration.")

    @staticmethod
    def _validate_path(relative_path: str) -> None:
        path = Path(relative_path)
        normalized = relative_path.replace("\\", "/")
        if path.is_absolute() or ".." in path.parts or normalized.startswith(PROTECTED_PREFIXES):
            raise ValueError(f"Protected or unsafe proposal path: {relative_path}")
        if not normalized.startswith(("skills/", "tests/", "prompts/")):
            raise ValueError("Proposals may only add or change skills, tests, or prompts.")


class ImprovementValidator:
    def __init__(self, root: Path = SCRATCH_DIR / "improvement-proposals"):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def validate(self, proposal: ImprovementProposal) -> dict:
        proposal.validate()
        proposal_id = uuid.uuid4().hex
        workspace = self.root / proposal_id
        workspace.mkdir()
        try:
            for relative_path, content in {**proposal.files, **proposal.tests}.items():
                destination = workspace / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(str(content), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "tests"],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={
                    "PATH": str(Path(sys.executable).parent),
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                },
            )
            report = {
                "proposal_id": proposal_id,
                "status": "validated" if result.returncode == 0 else "rejected",
                "tests": result.stdout[-4000:],
                "errors": result.stderr[-4000:],
                "requires_human_approval": True,
                "activation": "not performed",
            }
            (workspace / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            return report
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "proposal_id": proposal_id,
                "status": "rejected",
                "errors": str(error),
                "requires_human_approval": True,
                "activation": "not performed",
            }
