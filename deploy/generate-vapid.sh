#!/bin/sh
set -eu

SECRETS_FILE="${HSX_SECRETS_FILE:-/etc/hsx/secrets.env}"
HSX_GROUP="${HSX_GROUP:-hsx}"

python3 - "$SECRETS_FILE" "$HSX_GROUP" <<'PY'
import base64
import grp
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

destination = Path(sys.argv[1])
group_id = grp.getgrnam(sys.argv[2]).gr_gid
private_key = ec.generate_private_key(ec.SECP256R1())
private_number = private_key.private_numbers().private_value.to_bytes(32, "big")
public_key = private_key.public_key().public_bytes(
    serialization.Encoding.X962,
    serialization.PublicFormat.UncompressedPoint,
)

def encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

existing = destination.read_text(encoding="utf-8").splitlines() if destination.exists() else []
existing = [
    line for line in existing
    if not line.startswith(("HSX_VAPID_PRIVATE_KEY=", "HSX_VAPID_PUBLIC_KEY="))
]
existing.extend([
    f"HSX_VAPID_PRIVATE_KEY={encode(private_number)}",
    f"HSX_VAPID_PUBLIC_KEY={encode(public_key)}",
])
destination.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
try:
    os.fchown(fd, 0, group_id)
    os.fchmod(fd, 0o640)
    os.write(fd, ("\n".join(existing) + "\n").encode("utf-8"))
finally:
    os.close(fd)
PY

printf 'VAPID keys saved to %s (root:%s, mode 0640).\n' "$SECRETS_FILE" "$HSX_GROUP"
