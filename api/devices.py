from __future__ import annotations

from agent.devices import resolve_target_device as _resolve_target_device


def resolve_target_device(target: str, client_host: str) -> str:
    return _resolve_target_device(target, client_host)
