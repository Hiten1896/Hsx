"""Run the broker as the dedicated root-owned systemd service."""

import os
from pathlib import Path

from .interfaces import default_hsx_uid
from .server import Broker, serve


def main() -> None:
    broker = Broker(
        Path(os.getenv("HSX_POLICY", "/etc/hsx/policy.yaml")),
        Path(os.getenv("HSX_AUDIT", "/var/log/hsx/audit.jsonl")),
        expected_uid=default_hsx_uid(),
    )
    serve(Path(os.getenv("HSX_BROKER_SOCKET", "/run/hsx/broker.sock")), broker)


if __name__ == "__main__":
    main()
