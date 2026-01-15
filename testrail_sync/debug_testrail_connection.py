#!/usr/bin/env python
"""
debug_testrail_connection.py

Helper script to verify that:
- config.json is present and has the expected structure:
    {
      "testrail": {
        "url": "...",
        "username": "...",
        "api_key": "...",
        "project_id": 293,
        "suite_id": null,
        "parent_section_id": 8990
      },
      "options": {
        "create_sections": true,
        "map_folders_to_sections": true
      }
    }
- TestRail API is reachable with those credentials.
- The given project_id exists and we can list its sections.

It also supports both response formats for get_sections:
- A plain JSON array of sections
- Or a JSON object with a "sections" array and paging info
"""

import json
import sys
from pathlib import Path

import requests


def load_config(config_path: Path) -> dict:
    """Load config.json and validate the 'testrail' section."""
    if not config_path.exists():
        print(f"[ERROR] config.json not found at: {config_path}")
        sys.exit(1)

    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "testrail" not in cfg or not isinstance(cfg["testrail"], dict):
        print("[ERROR] 'testrail' section is missing or not an object in config.json.")
        sys.exit(1)

    testrail_cfg = cfg["testrail"]

    required_keys = ["url", "username", "api_key", "project_id"]
    missing = [k for k in required_keys if k not in testrail_cfg]
    if missing:
        print(f"[ERROR] Missing keys inside 'testrail' section: {', '.join(missing)}")
        sys.exit(1)

    return testrail_cfg


def extract_sections(data):
    """Normalize sections JSON into a list.

    Some TestRail responses return a plain array:
        [ {..section..}, {..section..}, ... ]

    Others return an object with paging info:
        {
          "offset": 0,
          "limit": 250,
          "size": 5,
          "_links": {...},
          "sections": [ {..}, {..}, ... ]
        }
    """
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        if "sections" in data and isinstance(data["sections"], list):
            return data["sections"]

    # Fallback: unknown format
    print("[WARN] get_sections returned an unexpected JSON structure.")
    print("       Raw JSON:", data)
    return []


def main() -> int:
    project_root = Path(__file__).resolve().parent
    config_path = project_root / "config.json"

    print("Using config file:", config_path)
    tcfg = load_config(config_path)

    base_url = tcfg["url"].rstrip("/")
    username = tcfg["username"]
    api_key = tcfg["api_key"]
    project_id = tcfg["project_id"]

    # Make sure project_id is an int
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        print(f"[ERROR] project_id is not a valid integer: {project_id!r}")
        return 1

    session = requests.Session()
    session.auth = (username, api_key)

    # 1) Get project info
    url_project = f"{base_url}/index.php?/api/v2/get_project/{project_id}"
    print("\n[1] Checking project info:", url_project)

    resp = session.get(url_project)
    print("  Status code:", resp.status_code)
    if resp.status_code != 200:
        print("  Response text:", resp.text)
        print("[ERROR] Could not retrieve project. Check URL, project_id, or credentials.")
        return 1

    proj = resp.json()
    print("  OK. Project name:", proj.get("name"))

    # 2) List sections for this project
    url_sections = f"{base_url}/index.php?/api/v2/get_sections/{project_id}"
    print("\n[2] Listing sections:", url_sections)

    resp = session.get(url_sections)
    print("  Status code:", resp.status_code)
    if resp.status_code != 200:
        print("  Response text:", resp.text)
        print("[ERROR] Could not list sections. Check permissions.")
        return 1

    raw_data = resp.json()
    sections = extract_sections(raw_data)

    print(f"  OK. Found {len(sections)} sections.")
    if sections:
        print("  Sections:")
        for s in sections:
            sid = s.get("id")
            name = s.get("name")
            parent_id = s.get("parent_id")
            print(f"   - id={sid}, parent_id={parent_id}, name={name!r}")

    print("\n[OK] TestRail basic connectivity looks fine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
