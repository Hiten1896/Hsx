#!/usr/bin/env python3
"""Export locally stored Hsx feedback as redacted JSON Lines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.feedback import FeedbackStore  # noqa: E402
from config import DATA_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Export locally stored Hsx feedback.")
    parser.add_argument("--format", choices=("jsonl",), default="jsonl")
    parser.add_argument("--since", help="Include feedback created on or after YYYY-MM-DD.")
    parser.add_argument(
        "--include-file-contents",
        action="store_true",
        help="Permit file-content export only when enabled in Hsx feedback settings.",
    )
    args = parser.parse_args()
    try:
        records = FeedbackStore(DATA_DIR / "feedback.db").export_records(
            since=args.since,
            include_file_contents=args.include_file_contents,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for record in records:
        print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
