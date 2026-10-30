#!/usr/bin/env python3
"""Download and install a verified Vosk model for local voice input."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != destination and destination not in target.parents:
            raise RuntimeError(f"Unsafe model archive member: {member.filename}")
    archive.extractall(destination)


def install_model(destination: Path, expected_sha256: str, url: str = MODEL_URL) -> Path:
    destination = destination.expanduser().resolve()
    if destination.name != MODEL_NAME:
        raise ValueError(f"Destination must end with {MODEL_NAME}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hsx-vosk-") as temporary:
        archive_path = Path(temporary) / f"{MODEL_NAME}.zip"
        urllib.request.urlretrieve(url, archive_path)
        actual = _sha256(archive_path)
        if actual.lower() != expected_sha256.lower():
            raise RuntimeError(f"Model checksum mismatch: expected {expected_sha256}, got {actual}")
        extract_root = Path(temporary) / "extracted"
        extract_root.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, extract_root)
        source = extract_root / MODEL_NAME
        if not source.is_dir():
            raise RuntimeError(f"Downloaded archive does not contain {MODEL_NAME}")
        staging = destination.with_name(f".{MODEL_NAME}.staging")
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging)
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the verified local Hsx Vosk model.")
    parser.add_argument("--sha256", required=True, help="Expected SHA-256 checksum of the model ZIP archive")
    parser.add_argument("--destination", type=Path, default=Path("models") / MODEL_NAME)
    parser.add_argument("--url", default=MODEL_URL)
    args = parser.parse_args()
    print(f"Installing {MODEL_NAME} from {args.url}")
    print(f"Installed model at {install_model(args.destination, args.sha256, args.url)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
