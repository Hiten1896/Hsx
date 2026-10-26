"""Platform boundaries used by the broker.

The fake implementation is intentionally usable on Windows so policy and
request handling can be tested without Linux-only system calls.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Protocol


class SystemInterface(Protocol):
    def peer_uid(self, connection: object) -> int | None: ...

    def run(self, executable: str, arguments: list[str]) -> tuple[int, str, str]: ...

    def stats(self) -> dict[str, str | int]: ...


class LinuxSystemInterface:
    """Linux implementation; commands are invoked without a shell."""

    def peer_uid(self, connection: object) -> int | None:
        import socket
        import struct

        if not isinstance(connection, socket.socket):
            return None
        if not hasattr(socket, "SO_PEERCRED"):
            return None
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        return struct.unpack("3i", credentials)[0]

    def run(self, executable: str, arguments: list[str]) -> tuple[int, str, str]:
        completed = subprocess.run(
            [executable, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def stats(self) -> dict[str, str | int]:
        return {
            "platform": platform.platform(),
            "disk_free_bytes": shutil.disk_usage("/").free,
        }


@dataclass
class FakeSystemInterface:
    """Deterministic platform substitute for unit tests."""

    uid: int = 1000
    results: dict[str, tuple[int, str, str]] = field(default_factory=dict)
    calls: list[tuple[str, list[str]]] = field(default_factory=list)

    def peer_uid(self, connection: object) -> int | None:
        return self.uid

    def run(self, executable: str, arguments: list[str]) -> tuple[int, str, str]:
        self.calls.append((executable, list(arguments)))
        return self.results.get(executable, (0, "", ""))

    def stats(self) -> dict[str, str | int]:
        return {"platform": "fake", "disk_free_bytes": 1_000_000_000}


def default_system() -> SystemInterface:
    if os.name == "posix":
        return LinuxSystemInterface()
    return FakeSystemInterface()


def default_hsx_uid() -> int | None:
    if os.name != "posix":
        return None
    import pwd

    try:
        return pwd.getpwnam("hsx").pw_uid
    except KeyError:
        return None
