#!/usr/bin/env python
"""
sync_newman_to_testrail.py

Sync a Postman collection structure into TestRail.

Mapping:

- Create (or reuse) ONE section in TestRail with the NAME OF THE COLLECTION
  under parent_section_id from config.json (if provided).
- Put ALL test cases inside that single section.
- Each request becomes a test case with title:

      [slug_folder_name]: <request_name>

  where slug_folder_name is a simple slug from the Postman folder name.

Supports:
- --dry-run      : preview only, no changes in TestRail
- --folder NAME  : only sync a specific Postman folder (by name)
- --collection   : path to the Postman collection JSON
- --config       : path to config.json

Expected config.json structure:

{
  "testrail": {
    "url": "https://mazdausa.testrail.com",
    "username": "user@example.com",
    "api_key": "xxx",
    "project_id": 293,
    "suite_id": null,
    "parent_section_id": 8990
  },
  "options": {
    "create_sections": true
  }
}
"""

import argparse
import json
import sys
import re
from pathlib import Path
from typing import Dict, Tuple, Optional, List

import requests


# ---------- Config helpers ----------


def load_config(config_path: Path) -> dict:
    """Load config.json and extract the 'testrail' and 'options' sections."""
    if not config_path.exists():
        print(f"[ERROR] config.json not found at: {config_path}")
        sys.exit(1)

    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "testrail" not in cfg or not isinstance(cfg["testrail"], dict):
        print("[ERROR] 'testrail' section missing or invalid in config.json.")
        sys.exit(1)

    testrail_cfg = cfg["testrail"]
    options_cfg = cfg.get("options", {})

    required_keys = ["url", "username", "api_key", "project_id"]
    missing = [k for k in required_keys if k not in testrail_cfg]
    if missing:
        print(f"[ERROR] Missing keys in 'testrail' section: {', '.join(missing)}")
        sys.exit(1)

    return {
        "testrail": testrail_cfg,
        "options": options_cfg,
    }


# ---------- TestRail helpers ----------


def testrail_session(tcfg: dict) -> tuple[requests.Session, str]:
    """Build a requests.Session configured for TestRail."""
    base_url = tcfg["url"].rstrip("/")
    username = tcfg["username"]
    api_key = tcfg["api_key"]

    session = requests.Session()
    session.auth = (username, api_key)
    return session, base_url


def fetch_sections(session: requests.Session, base_url: str, project_id: int,
                   suite_id: Optional[int]) -> List[dict]:
    """Fetch all sections for a project (and suite if provided)."""
    if suite_id is not None:
        url = f"{base_url}/index.php?/api/v2/get_sections/{project_id}&suite_id={suite_id}"
    else:
        url = f"{base_url}/index.php?/api/v2/get_sections/{project_id}"

    print(f"[API] GET sections -> {url}")
    resp = session.get(url)
    print(f"[API] -> status {resp.status_code}")
    if resp.status_code != 200:
        print("[ERROR] Failed to fetch sections from TestRail.")
        print("        Response:", resp.text)
        sys.exit(1)

    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("sections"), list):
        return data["sections"]

    print("[WARN] Unexpected sections JSON structure, returning empty list.")
    print("       Raw JSON:", data)
    return []


def build_section_index(sections: List[dict]) -> Dict[Tuple[str, Optional[int]], dict]:
    """Build an index: (name, parent_id) -> section dict."""
    idx: Dict[Tuple[str, Optional[int]], dict] = {}
    for s in sections:
        key = (s.get("name"), s.get("parent_id"))
        idx[key] = s
    return idx


def ensure_section(
    session: requests.Session,
    base_url: str,
    project_id: int,
    suite_id: Optional[int],
    parent_section_id: Optional[int],
    name: str,
    sections_index: Dict[Tuple[str, Optional[int]], dict],
    dry_run: bool,
) -> Optional[int]:
    """Ensure a section with given name/parent exists. Return its ID."""
    key = (name, parent_section_id)
    existing = sections_index.get(key)
    if existing:
        sid = existing.get("id")
        print(f"[SECTION] Reusing existing section id={sid} name={name!r} parent={parent_section_id}")
        return sid

    if dry_run:
        print(f"[DRY-RUN] Would create COLLECTION section name={name!r} parent={parent_section_id}")
        return None

    # Real creation
    if suite_id is not None:
        endpoint = f"{base_url}/index.php?/api/v2/add_section/{project_id}&suite_id={suite_id}"
    else:
        endpoint = f"{base_url}/index.php?/api/v2/add_section/{project_id}"

    payload = {"name": name}
    if parent_section_id is not None:
        payload["parent_id"] = parent_section_id

    print(f"[API] POST add_section -> {endpoint}")
    print(f"      payload={payload}")
    resp = session.post(endpoint, json=payload)
    print(f"[API] -> status {resp.status_code}")
    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to create collection section.")
        print("        Body:", resp.text)
        sys.exit(1)

    s = resp.json()
    sid = s.get("id")
    print(f"[OK] Collection section created id={sid} name={s.get('name')!r} parent={s.get('parent_id')}")

    sections_index[key] = s
    return sid


def fetch_cases_for_section(
    session: requests.Session,
    base_url: str,
    project_id: int,
    section_id: int,
    suite_id: Optional[int],
) -> list:
    """Fetch all cases for a given section."""
    if suite_id is not None:
        url = f"{base_url}/index.php?/api/v2/get_cases/{project_id}&suite_id={suite_id}&section_id={section_id}"
    else:
        url = f"{base_url}/index.php?/api/v2/get_cases/{project_id}&section_id={section_id}"

    print(f"[API] GET cases for section {section_id} -> {url}")
    resp = session.get(url)
    print(f"[API] -> status {resp.status_code}")
    if resp.status_code != 200:
        print("[ERROR] Failed to fetch cases for section.")
        print("        Body:", resp.text)
        sys.exit(1)

    data = resp.json()
    if isinstance(data, list):
        return data

    print("[WARN] Unexpected cases JSON structure, returning empty list.")
    print("       Raw JSON:", data)
    return []


def build_case_index(cases: list) -> Dict[str, dict]:
    """Index cases by title."""
    idx: Dict[str, dict] = {}
    for c in cases:
        title = c.get("title")
        if title:
            idx[title] = c
    return idx


def ensure_case(
    session: requests.Session,
    base_url: str,
    project_id: int,
    section_id: int,
    suite_id: Optional[int],
    title: str,
    dry_run: bool,
    existing_cases_idx: Dict[str, dict],
) -> Optional[int]:
    """Ensure a test case exists in a section with given title."""
    existing = existing_cases_idx.get(title)
    if existing:
        cid = existing.get("id")
        print(f"[CASE] Reusing existing case id={cid} title={title!r} in section={section_id}")
        return cid

    if dry_run:
        print(f"[DRY-RUN] Would create case title={title!r} in section={section_id}")
        return None

    endpoint = f"{base_url}/index.php?/api/v2/add_case/{section_id}"
    payload = {
        "title": title,
    }

    print(f"[API] POST add_case -> {endpoint}")
    print(f"      payload={payload}")
    resp = session.post(endpoint, json=payload)
    print(f"[API] -> status {resp.status_code}")
    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to create case.")
        print("        Body:", resp.text)
        sys.exit(1)

    c = resp.json()
    cid = c.get("id")
    print(f"[OK] Case created id={cid} title={c.get('title')!r} in section={section_id}")

    existing_cases_idx[title] = c
    return cid


# ---------- Helpers for collection ----------


def slugify(name: str) -> str:
    """Very simple slug: lowercase, spaces -> '-', remove non-alphanumeric/-/_."""
    if not name:
        return "root"
    s = name.strip().lower()
    s = s.replace(" ", "-")
    s = re.sub(r"[^a-z0-9\-_]", "", s)
    return s or "root"


def load_collection(path: Path) -> dict:
    if not path.exists():
        print(f"[ERROR] Collection not found at: {path}")
        sys.exit(1)

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_folder_requests(collection: dict, folder_filter: Optional[str] = None):
    """
    Yield (folder_name, request_item) pairs.

    Assumes:
    - Top-level "item" is a list.
    - Elements with "item" list inside are folders.
    - Sub-items with "request" are requests.
    """
    items = collection.get("item", [])
    for item in items:
        # Folder (has .item list)
        if "item" in item and isinstance(item["item"], list):
            folder_name = item.get("name", "<no name>")
            if folder_filter and folder_name != folder_filter:
                continue

            for sub in item["item"]:
                if "request" in sub:
                    yield folder_name, sub

        # Optional: top-level request (without folder) mapped to "<root>"
        elif "request" in item:
            folder_name = "<root>"
            if folder_filter:
                continue
            yield folder_name, item


# ---------- Main ----------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sync a Postman collection into a single TestRail section (collection name) "
            "with prefixed test case titles '[slug_folder]: request_name'."
        )
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="Path to the Postman collection JSON.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to config.json with TestRail settings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, do not write changes to TestRail.",
    )
    parser.add_argument(
        "--folder",
        help="Folder name in the Postman collection to sync. If omitted, all folders are processed.",
    )

    args = parser.parse_args()

    collection_path = Path(args.collection).resolve()
    config_path = Path(args.config).resolve()
    dry_run = args.dry_run
    folder_filter = args.folder

    cfg = load_config(config_path)
    tcfg = cfg["testrail"]
    options = cfg.get("options", {})

    project_id = int(tcfg["project_id"])
    suite_id = tcfg.get("suite_id")
    if suite_id is not None:
        try:
            suite_id = int(suite_id)
        except (TypeError, ValueError):
            print(f"[WARN] suite_id is not a valid integer: {suite_id!r}. Ignoring.")
            suite_id = None

    parent_section_id = tcfg.get("parent_section_id")
    if parent_section_id is not None:
        try:
            parent_section_id = int(parent_section_id)
        except (TypeError, ValueError):
            print(f"[WARN] parent_section_id is not a valid integer: {parent_section_id!r}. Ignoring.")
            parent_section_id = None

    create_sections = bool(options.get("create_sections", True))

    collection = load_collection(collection_path)
    collection_name = collection.get("info", {}).get("name") or collection_path.stem

    print("===========================================================")
    print(" sync_newman_to_testrail.py")
    print("===========================================================")
    print(f" Project ID        : {project_id}")
    print(f" Collection name   : {collection_name}")
    print(f" Collection path   : {collection_path}")
    print(f" Config            : {config_path}")
    print(f" Folder filter     : {folder_filter or '(ALL folders)'}")
    print(f" Mode              : { 'DRY-RUN' if dry_run else 'REAL SYNC' }")
    print(f" Parent section ID : {parent_section_id}")
    print("===========================================================\n")

    session, base_url = testrail_session(tcfg)

    # Fetch existing sections once
    sections = fetch_sections(session, base_url, project_id, suite_id)
    sections_idx = build_section_index(sections)
    print(f"[INFO] Loaded {len(sections_idx)} existing sections into index.\n")

    # Ensure ONE section with the collection name under parent_section_id
    if create_sections:
        collection_section_id = ensure_section(
            session=session,
            base_url=base_url,
            project_id=project_id,
            suite_id=suite_id,
            parent_section_id=parent_section_id,
            name=collection_name,
            sections_index=sections_idx,
            dry_run=dry_run,
        )
    else:
        key = (collection_name, parent_section_id)
        existing_section = sections_idx.get(key)
        if not existing_section:
            print("[ERROR] Collection section does not exist and create_sections=False.")
            print(f"        Expected section name={collection_name!r}, parent_id={parent_section_id}")
            return 1
        collection_section_id = existing_section.get("id")

    if dry_run:
        print(f"Collection: {collection_name}")
        print(f"Target TestRail project_id: {project_id}\n")

    # Fetch existing cases once for that collection section (only in real mode)
    cases_idx: Dict[str, dict] = {}
    if not dry_run and collection_section_id is not None:
        existing_cases = fetch_cases_for_section(
            session, base_url, project_id, collection_section_id, suite_id
        )
        cases_idx = build_case_index(existing_cases)
        print(f"[INFO] Loaded {len(cases_idx)} existing cases in collection section.\n")

    folders_seen = set()
    total_requests = 0
    total_cases_created = 0
    total_cases_reused = 0
    printed_folders = set()

    for folder_name, request_item in iter_folder_requests(collection, folder_filter):
        folders_seen.add(folder_name)
        request_name = request_item.get("name", "<no name>")
        total_requests += 1

        # Build test case title with slug prefix
        slug = "root" if folder_name == "<root>" else slugify(folder_name)
        case_title = f"[{slug}]: {request_name}"

        if dry_run:
            if folder_name not in printed_folders:
                print(
                    f"[Folder] {folder_name} -> test cases will be created under collection section '{collection_name}'"
                )
                printed_folders.add(folder_name)
            print(f"  - Create test case: {case_title}")
            continue

        if collection_section_id is None:
            print("[ERROR] collection_section_id is None in real sync mode.")
            return 1

        before = len(cases_idx)
        cid = ensure_case(
            session=session,
            base_url=base_url,
            project_id=project_id,
            section_id=collection_section_id,
            suite_id=suite_id,
            title=case_title,
            dry_run=False,
            existing_cases_idx=cases_idx,
        )

        after = len(cases_idx)
        if after > before:
            total_cases_created += 1
        else:
            total_cases_reused += 1

    print("\n===========================================================")
    print(" Summary")
    print("===========================================================")
    print(f" Collection section: {collection_name!r}")
    print(f" Folders seen      : {sorted(folders_seen)}")
    print(f" Total requests    : {total_requests}")
    if dry_run:
        print(" Mode              : DRY-RUN (no changes were made)")
    else:
        print(" Mode              : REAL SYNC (changes were attempted)")
        print(f" Cases created     : {total_cases_created}")
        print(f" Cases reused      : {total_cases_reused}")
    print("===========================================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
