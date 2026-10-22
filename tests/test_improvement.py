from pathlib import Path

import pytest

from agent.improvement import ImprovementProposal, ImprovementValidator
from agent.skill_loader import load_skills


def test_proposal_requires_tests_and_protects_core():
    with pytest.raises(ValueError):
        ImprovementProposal.from_dict({"name": "x", "rationale": "r", "files": {"agent/new.py": "x"}, "tests": {}})
    with pytest.raises(ValueError):
        ImprovementProposal.from_dict(
            {"name": "x", "rationale": "r", "files": {"config.py": "x"}, "tests": {"tests/test_x.py": "def test_x(): pass"}}
        )


def test_valid_proposal_is_validated_without_activation(tmp_path: Path):
    proposal = ImprovementProposal.from_dict(
        {
            "name": "safe skill",
            "rationale": "Adds a small isolated skill.",
            "files": {"skills/example/skill.py": "VALUE = 1\n"},
            "tests": {"tests/test_example.py": "def test_value():\n    assert 1 == 1\n"},
        }
    )
    report = ImprovementValidator(tmp_path).validate(proposal)
    assert report["status"] == "validated"
    assert report["requires_human_approval"] is True
    assert report["activation"] == "not performed"


def test_improvement_skill_is_loaded_with_consent():
    tools, _ = load_skills("skills")
    proposal = next(tool for tool in tools if tool["name"] == "propose_improvement")
    assert proposal["requires_acknowledgement"] is True
    assert proposal["risk"] == "confirm"
