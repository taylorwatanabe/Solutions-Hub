#!/usr/bin/env python3
"""Seed Solutions Hub submissions from a CSV into Sheets (or local JSON)."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.dotenv_util import load_dotenv_files
from integrations import sheets_store

load_dotenv_files()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="Path to verified dataset CSV")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows instead of replacing the store (Sheets: append only path not implemented; still replaces)",
    )
    args = parser.parse_args()

    if not args.csv_path.is_file():
        print(f"CSV not found: {args.csv_path}", file=sys.stderr)
        return 1

    with args.csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]

    # Normalize common header aliases
    aliases = {
        "email": "submitter_email",
        "name": "submitter_name",
        "submitter": "submitter_name",
        "dept": "department",
        "solution_a": "option_a",
        "solution_b": "option_b",
        "solution_c": "option_c",
        "rec": "recommendation",
        "roi": "estimated_value",
    }
    normalized = []
    for row in rows:
        item = {}
        for k, v in row.items():
            key = (k or "").strip()
            lower = key.lower().replace(" ", "_")
            dest = aliases.get(lower, lower)
            if dest in sheets_store.HEADERS:
                item[dest] = v
        if item:
            normalized.append(item)

    if args.append:
        existing = sheets_store.list_submissions()
        normalized = existing + normalized

    count = sheets_store.replace_all_submissions(normalized)
    print(f"Wrote {count} submissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
