#!/usr/bin/env python
"""
testrail_step1_connect.py

Step 1:
- Read config.json
- Parse CLI arguments
- Test connection to TestRail:
    - GET /get_project/{project_id}
    - Print project name if OK
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

import requests


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load config.json and return the 'testrail' section."""
    if not config_path.exists():
        print(f"[ERROR] config.json not found at: {config_path}")
        sys.exit(1)

    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "testrail" not in cfg or not isinstance(cfg["testrail"], dict):
        print("[ERROR] 'testrail' section missing or invalid in config.json.")
        sys.exit(1)

    tcfg = cfg["testrail"]

    required_keys = ["url", "username", "api_key", "project_id"]
    missing = [k for k in required_keys if k not in tcfg]
    if missing:
        print(f"[ERROR] Missing keys in 'testrail' section: {', '.join(missing)}")
        sys.exit(1)

    return tcfg


def build_session(tcfg: Dict[str, Any]) -> tuple[requests.Session, str, int]:
    """Return (session, base_url, project_id)."""
    base_url = tcfg["url"].rstrip("/")
    username = tcfg["username"]
    api_key = tcfg["api_key"]
    project_id = int(tcfg["project_id"])

    session = requests.Session()
    session.auth = (username, api_key)

    return session, base_url, project_id


def test_connection(session: requests.Session, base_url: str, project_id: int) -> None:
    """Call get_project/{project_id} and print basic info."""
    url = f"{base_url}/index.php?/api/v2/get_project/{project_id}"

    print("===========================================================")
    print(" TestRail connection test")
    print("===========================================================")
    print(f"[INFO] GET {url}")

    resp = session.get(url)
    print(f"[INFO] Status code: {resp.status_code}")

    if resp.status_code != 200:
        print("[ERROR] Could not fetch project info.")
        print("        Response body:", resp.text)
        sys.exit(1)

    data = resp.json()
    name = data.get("name")
    print(f"[OK] Connected to TestRail project: {name!r} (id={project_id})")
    print("===========================================================")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Step 1: test connection to TestRail using config.json"
    )
    parser.add_argument(
        "--config",
        help="Path to config.json (default: ../config.json from this script).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="For future steps; here it's only parsed (no effect yet).",
    )
    return parser


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    # Default config path: project_root/config.json
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent  # go up from python_scripts/ to repo root
    default_config = project_root / "config.json"

    config_path = Path(args.config) if args.config else default_config

    print("===========================================================")
    print(" Step 1 - Config & Connection")
    print("===========================================================")
    print(f" Script       : {script_path}")
    print(f" Project root : {project_root}")
    print(f" Config path  : {config_path}")
    print(f" Dry-run      : {args.dry_run}")
    print("===========================================================\n")

    tcfg = load_config(config_path)
    session, base_url, project_id = build_session(tcfg)

    test_connection(session, base_url, project_id)

    print("[INFO] Step 1 completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
