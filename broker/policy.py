"""Strict YAML policy loading for broker action classes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class PolicyError(ValueError):
    """Raised when a policy is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class ActionPolicy:
    tier: str
    constraints: dict[str, Any]


@dataclass(frozen=True)
class Policy:
    autonomy: str
    actions: dict[str, ActionPolicy]

    @classmethod
    def load(cls, path: Path) -> "Policy":
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PolicyError("Policy file is unavailable or invalid.") from exc
        if not isinstance(data, dict):
            raise PolicyError("Policy must be a mapping.")
        autonomy = data.get("autonomy", "shadow")
        if autonomy not in {"shadow", "assisted", "auto"}:
            raise PolicyError("Policy autonomy must be shadow, assisted, or auto.")
        raw_actions = data.get("actions")
        if not isinstance(raw_actions, dict) or not raw_actions:
            raise PolicyError("Policy must define actions.")
        actions: dict[str, ActionPolicy] = {}
        for name, raw in raw_actions.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                raise PolicyError("Each action policy must be a mapping.")
            tier = raw.get("tier")
            if tier not in {"auto", "approve", "deny"}:
                raise PolicyError(f"Invalid tier for action {name}.")
            constraints = raw.get("constraints", {})
            if not isinstance(constraints, dict):
                raise PolicyError(f"Constraints for action {name} must be a mapping.")
            actions[name] = ActionPolicy(tier=tier, constraints=constraints)
        return cls(autonomy=autonomy, actions=actions)

    def action(self, name: str) -> ActionPolicy:
        try:
            return self.actions[name]
        except KeyError as exc:
            raise PolicyError("Unknown action.") from exc
