#!/usr/bin/env python
"""
DEBUG VERSION of sync_newman_to_testrail.py

Goal:
- Confirm that THIS file is the one Python is executing.
- Confirm that it accepts --folder without error.
"""

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="DEBUG sync script (args only)")
    parser.add_argument(
        "--collection",
        required=True,
        help="Path to the Postman collection JSON.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to config.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, do not write changes.",
    )
    parser.add_argument(
        "--folder",
        help="Optional folder filter (by name).",
    )

    args = parser.parse_args()

    print("===========================================================")
    print(" DEBUG sync_newman_to_testrail.py")
    print("===========================================================")
    print(f" Script path : {Path(__file__).resolve()}")
    print(" Parsed args :")
    for k, v in vars(args).items():
        print(f"  - {k} = {v!r}")
    print("===========================================================")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
