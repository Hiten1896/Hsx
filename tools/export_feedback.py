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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export locally stored Hsx feedback.")
    parser.add_argument("--format", choices=("jsonl",), default="jsonl")
    parser.add_argument("--since", help="Include feedback created on or after YYYY-MM-DD.")
    parser.add_argument(
        "--database",
        type=Path,
        default=DATA_DIR / "feedback.db",
        help="Feedback SQLite database (default: data/feedback.db).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSONL to this file; stdout is used when omitted.",
    )
    parser.add_argument(
        "--include-file-contents",
        action="store_true",
        help="Permit file-content export only when enabled in Hsx feedback settings.",
    )
    args = parser.parse_args(argv)
    try:
        records = FeedbackStore(args.database).export_records(
            since=args.since,
            include_file_contents=args.include_file_contents,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    lines = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    if args.output is None:
        sys.stdout.buffer.write(lines.encode("utf-8"))
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(lines, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
