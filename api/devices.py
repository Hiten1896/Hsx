from __future__ import annotations

from agent.devices import resolve_target_device as _resolve_target_device


def resolve_target_device(
    target: str,
    client_host: str,
    *,
    allow_remote_local_host: bool = False,
) -> str:
    return _resolve_target_device(
        target,
        client_host,
        allow_remote_local_host=allow_remote_local_host,
    )
