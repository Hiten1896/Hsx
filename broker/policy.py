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
    permission: str | None
    constraints: dict[str, Any]


@dataclass(frozen=True)
class Policy:
    actions: dict[str, ActionPolicy]

    @classmethod
    def load(cls, path: Path) -> "Policy":
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PolicyError("Policy file is unavailable or invalid.") from exc
        if not isinstance(data, dict):
            raise PolicyError("Policy must be a mapping.")
        if set(data) != {"actions"}:
            raise PolicyError("Policy must contain only the action allowlist.")
        raw_actions = data.get("actions")
        if not isinstance(raw_actions, dict) or not raw_actions:
            raise PolicyError("Policy must define actions.")
        actions: dict[str, ActionPolicy] = {}
        for name, raw in raw_actions.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                raise PolicyError("Each action policy must be a mapping.")
            if set(raw) - {"permission", "constraints"}:
                raise PolicyError(f"Action policy for {name} has an unsupported field.")
            permission = raw.get("permission")
            if permission is not None and (not isinstance(permission, str) or not permission):
                raise PolicyError(f"Permission for action {name} must be a non-empty action name.")
            constraints = raw.get("constraints", {})
            if not isinstance(constraints, dict):
                raise PolicyError(f"Constraints for action {name} must be a mapping.")
            actions[name] = ActionPolicy(permission=permission, constraints=constraints)
        return cls(actions=actions)

    def action(self, name: str) -> ActionPolicy:
        try:
            return self.actions[name]
        except KeyError as exc:
            raise PolicyError("Unknown action.") from exc
