"""Typed request validation. No request contains a shell command."""

from __future__ import annotations

import re
from typing import Any


class SchemaError(ValueError):
    """Raised for malformed or unsafe broker requests."""


ACTION_FIELDS: dict[str, dict[str, type]] = {
    "service": {"operation": str, "unit": str},
    "apt_install": {"package": str},
    "apt_remove": {"package": str},
    "pip_install": {"package": str},
    "disk_cleanup": {"target": str},
    "journal_read": {"unit": str},
    "system_stats": {},
    "network_status": {},
    "tailscale_status": {},
    "schedule_timer": {"operation": str, "name": str},
    "reboot": {},
    "wifi_reconnect": {},
}
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@%=-]{0,127}$")


def validate_request(request: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(request, dict) or set(request) != {"id", "action", "args"}:
        raise SchemaError("Request must contain exactly id, action, and args.")
    request_id, action, args = request["id"], request["action"], request["args"]
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise SchemaError("Request id is invalid.")
    if not isinstance(action, str) or action not in ACTION_FIELDS:
        raise SchemaError("Unknown action.")
    if not isinstance(args, dict):
        raise SchemaError("Arguments must be an object.")
    expected = ACTION_FIELDS[action]
    if set(args) != set(expected):
        raise SchemaError("Arguments do not match the action schema.")
    for key, expected_type in expected.items():
        value = args[key]
        if type(value) is not expected_type:
            raise SchemaError(f"Argument {key} has the wrong type.")
        if isinstance(value, str) and (
            any(token in value for token in (";", "$(", "${", "\x00")) or ".." in value
        ):
            raise SchemaError(f"Argument {key} contains forbidden syntax.")
        if isinstance(value, str) and value and not SAFE_TOKEN.fullmatch(value):
            raise SchemaError(f"Argument {key} contains unsafe characters.")
    return action, args
