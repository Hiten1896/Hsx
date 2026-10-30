#!/usr/bin/env python3
"""Download and install the small Vosk model used by the local voice listener."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != destination and destination not in target.parents:
            raise RuntimeError(f"Unsafe model archive member: {member.filename}")
    archive.extractall(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the local Hsx Vosk voice model.")
    parser.add_argument("--destination", type=Path, default=None)
    parser.add_argument("--url", default=MODEL_URL)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    destination = args.destination or root / "models" / MODEL_NAME
    destination = destination.expanduser().resolve()
    if destination.is_dir() and any(destination.iterdir()):
        print(f"Voice model already exists: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hsx-vosk-") as temporary:
        archive_path = Path(temporary) / f"{MODEL_NAME}.zip"
        print(f"Downloading {args.url}")
        urllib.request.urlretrieve(args.url, archive_path)
        extract_root = Path(temporary) / "extracted"
        extract_root.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, extract_root)
        source = extract_root / MODEL_NAME
        if not source.is_dir():
            raise RuntimeError(f"Downloaded archive does not contain {MODEL_NAME}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(source), str(destination))
    print(f"Installed voice model: {destination}")


if __name__ == "__main__":
    main()
