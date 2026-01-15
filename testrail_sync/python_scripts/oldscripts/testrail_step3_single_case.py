#!/usr/bin/env python
"""
testrail_step3_single_case.py

Step 3:
- Read config.json
- Test connection to TestRail (get_project)
- Read Postman collection to get its name
- Ensure ONE TestRail section exists with that collection name
  under the configured parent_section_id (if any).
- Ensure ONE test case exists in that section with a fixed title
  (by default: "[debug]: BPXAPI connectivity test").

Behavior:
- Section:
    - Reuse if exists (same name + parent_id).
    - Create if not exists (unless --dry-run).
- Test case:
    - Reuse if a case with the same title already exists in that section.
    - Create if not exists (unless --dry-run).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ---------- Config & connection helpers ----------


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

    # Normalize project_id
    try:
        tcfg["project_id"] = int(tcfg["project_id"])
    except (TypeError, ValueError):
        print(f"[ERROR] project_id is not a valid integer: {tcfg.get('project_id')!r}")
        sys.exit(1)

    # Optional suite_id
    suite_id = tcfg.get("suite_id")
    if suite_id is not None:
        try:
            tcfg["suite_id"] = int(suite_id)
        except (TypeError, ValueError):
            print(f"[WARN] suite_id is not a valid integer: {suite_id!r}. Ignoring.")
            tcfg["suite_id"] = None

    # Optional parent_section_id
    parent_section_id = tcfg.get("parent_section_id")
    if parent_section_id is not None:
        try:
            tcfg["parent_section_id"] = int(parent_section_id)
        except (TypeError, ValueError):
            print(
                f"[WARN] parent_section_id is not a valid integer: "
                f"{parent_section_id!r}. Ignoring."
            )
            tcfg["parent_section_id"] = None

    return tcfg


def build_session(tcfg: Dict[str, Any]) -> tuple[requests.Session, str]:
    """Return (session, base_url)."""
    base_url = tcfg["url"].rstrip("/")
    username = tcfg["username"]
    api_key = tcfg["api_key"]

    session = requests.Session()
    session.auth = (username, api_key)

    return session, base_url


def test_connection(session: requests.Session, base_url: str, project_id: int) -> None:
    """Call get_project/{project_id} and print basic info."""
    url = f"{base_url}/index.php?/api/v2/get_project/{project_id}"

    print("-----------------------------------------------------------")
    print(" TestRail connection test")
    print("-----------------------------------------------------------")
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
    print("-----------------------------------------------------------\n")


# ---------- Sections helpers ----------


def fetch_sections(
    session: requests.Session,
    base_url: str,
    project_id: int,
    suite_id: Optional[int],
) -> List[Dict[str, Any]]:
    """Fetch all sections for the project (and suite if provided)."""
    if suite_id is not None:
        url = f"{base_url}/index.php?/api/v2/get_sections/{project_id}&suite_id={suite_id}"
    else:
        url = f"{base_url}/index.php?/api/v2/get_sections/{project_id}"

    print("-----------------------------------------------------------")
    print(" Fetching sections")
    print("-----------------------------------------------------------")
    print(f"[INFO] GET {url}")
    resp = session.get(url)
    print(f"[INFO] Status code: {resp.status_code}")

    if resp.status_code != 200:
        print("[ERROR] Failed to fetch sections from TestRail.")
        print("        Response:", resp.text)
        sys.exit(1)

    data = resp.json()
    if isinstance(data, list):
        sections = data
    elif isinstance(data, dict) and isinstance(data.get("sections"), list):
        sections = data["sections"]
    else:
        print("[WARN] Unexpected sections JSON structure, returning empty list.")
        print("       Raw JSON:", data)
        sections = []

    print(f"[INFO] Retrieved {len(sections)} sections.")
    print("-----------------------------------------------------------\n")
    return sections


def build_section_index(
    sections: List[Dict[str, Any]]
) -> Dict[Tuple[str, Optional[int]], Dict[str, Any]]:
    """Index sections by (name, parent_id)."""
    idx: Dict[Tuple[str, Optional[int]], Dict[str, Any]] = {}
    for s in sections:
        key = (s.get("name"), s.get("parent_id"))
        idx[key] = s
    return idx


def ensure_collection_section(
    session: requests.Session,
    base_url: str,
    project_id: int,
    suite_id: Optional[int],
    parent_section_id: Optional[int],
    collection_name: str,
    sections_idx: Dict[Tuple[str, Optional[int]], Dict[str, Any]],
    dry_run: bool,
) -> Optional[int]:
    """
    Ensure there is ONE section with:
        name      == collection_name
        parent_id == parent_section_id

    Returns the section_id (or None in dry-run if it would be created).
    """
    key = (collection_name, parent_section_id)
    existing = sections_idx.get(key)

    if existing:
        sid = existing.get("id")
        print(
            f"[SECTION] Reusing existing section id={sid} "
            f"name={collection_name!r} parent={parent_section_id}"
        )
        return sid

    # If not found, create (or simulate)
    if dry_run:
        print(
            f"[DRY-RUN] Would create collection section name={collection_name!r} "
            f"parent={parent_section_id}"
        )
        return None

    # Real creation
    if suite_id is not None:
        endpoint = (
            f"{base_url}/index.php?/api/v2/add_section/"
            f"{project_id}&suite_id={suite_id}"
        )
    else:
        endpoint = f"{base_url}/index.php?/api/v2/add_section/{project_id}"

    payload: Dict[str, Any] = {"name": collection_name}
    if parent_section_id is not None:
        payload["parent_id"] = parent_section_id

    print("-----------------------------------------------------------")
    print(" Creating collection section")
    print("-----------------------------------------------------------")
    print(f"[API] POST {endpoint}")
    print(f"[API] payload={payload}")
    resp = session.post(endpoint, json=payload)
    print(f"[API] Status code: {resp.status_code}")

    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to create collection section.")
        print("        Body:", resp.text)
        sys.exit(1)

    s = resp.json()
    sid = s.get("id")
    print(
        f"[OK] Collection section created id={sid} "
        f"name={s.get('name')!r} parent={s.get('parent_id')}"
    )
    print("-----------------------------------------------------------\n")

    # Update local index so later steps could reuse it if needed
    sections_idx[key] = s

    return sid


# ---------- Cases helpers ----------


def fetch_cases_for_section(
    session: requests.Session,
    base_url: str,
    project_id: int,
    section_id: int,
    suite_id: Optional[int],
) -> List[Dict[str, Any]]:
    """Fetch all cases for a given section."""
    if suite_id is not None:
        url = (
            f"{base_url}/index.php?/api/v2/get_cases/"
            f"{project_id}&suite_id={suite_id}&section_id={section_id}"
        )
    else:
        url = f"{base_url}/index.php?/api/v2/get_cases/{project_id}&section_id={section_id}"

    print("-----------------------------------------------------------")
    print(f" Fetching cases for section {section_id}")
    print("-----------------------------------------------------------")
    print(f"[INFO] GET {url}")
    resp = session.get(url)
    print(f"[INFO] Status code: {resp.status_code}")

    if resp.status_code != 200:
        print("[ERROR] Failed to fetch cases for section.")
        print("        Body:", resp.text)
        sys.exit(1)

    data = resp.json()
    if isinstance(data, list):
        cases = data
    else:
        print("[WARN] Unexpected cases JSON structure, returning empty list.")
        print("       Raw JSON:", data)
        cases = []

    print(f"[INFO] Retrieved {len(cases)} cases for section {section_id}.")
    print("-----------------------------------------------------------\n")
    return cases


def build_case_index(cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index cases by title."""
    idx: Dict[str, Dict[str, Any]] = {}
    for c in cases:
        title = c.get("title")
        if isinstance(title, str):
            idx[title] = c
    return idx


def ensure_debug_case(
    session: requests.Session,
    base_url: str,
    project_id: int,
    section_id: int,
    suite_id: Optional[int],
    title: str,
    dry_run: bool,
    cases_idx: Dict[str, Dict[str, Any]],
) -> Optional[int]:
    """
    Ensure a single debug test case exists in the given section.
    - If it exists (by title), reuse it.
    - If not, create it (unless dry_run).
    """
    existing = cases_idx.get(title)
    if existing:
        cid = existing.get("id")
        print(
            f"[CASE] Reusing existing debug case id={cid} "
            f"title={title!r} in section={section_id}"
        )
        return cid

    if dry_run:
        print(
            f"[DRY-RUN] Would create debug test case title={title!r} "
            f"in section={section_id}"
        )
        return None

    endpoint = f"{base_url}/index.php?/api/v2/add_case/{section_id}"
    payload: Dict[str, Any] = {
        "title": title,
        "custom_preconds": "Auto-generated debug case for BPXAPI/TestRail sync.",
        "custom_steps": "1) Run Postman/Newman tests\n2) Sync results into this run",
    }

    print("-----------------------------------------------------------")
    print(" Creating debug test case")
    print("-----------------------------------------------------------")
    print(f"[API] POST {endpoint}")
    print(f"[API] payload={payload}")
    resp = session.post(endpoint, json=payload)
    print(f"[API] Status code: {resp.status_code}")

    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to create debug case.")
        print("        Body:", resp.text)
        sys.exit(1)

    c = resp.json()
    cid = c.get("id")
    print(
        f"[OK] Debug case created id={cid} "
        f"title={c.get('title')!r} in section={section_id}"
    )
    print("-----------------------------------------------------------\n")

    # Update local index
    cases_idx[title] = c
    return cid


# ---------- Collection helpers ----------


def load_collection(collection_path: Path) -> Dict[str, Any]:
    """Load the Postman collection and return its JSON."""
    if not collection_path.exists():
        print(f"[ERROR] Collection JSON not found at: {collection_path}")
        sys.exit(1)

    with collection_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return data


def get_collection_name(collection_json: Dict[str, Any], fallback_path: Path) -> str:
    """Get collection name from info.name or use the file stem as fallback."""
    info = collection_json.get("info", {})
    name = info.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return fallback_path.stem


# ---------- CLI & main ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 3: ensure a single debug test case exists in the collection section."
        )
    )
    parser.add_argument(
        "--config",
        help="Path to config.json (default: ../config.json from this script).",
    )
    parser.add_argument(
        "--collection",
        help=(
            "Path to the Postman collection JSON. "
            "Default: ../newman/BPXAPI.postman_collection.json from this script."
        ),
    )
    parser.add_argument(
        "--title",
        default="[debug]: BPXAPI connectivity test",
        help=(
            "Title of the debug test case to ensure. "
            "Default: '[debug]: BPXAPI connectivity test'."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, do not write changes to TestRail.",
    )
    return parser


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    # Paths relative to this script
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent  # python_scripts/ -> repo root
    default_config = project_root / "config.json"
    default_collection = project_root / "newman" / "BPXAPI.postman_collection.json"

    config_path = Path(args.config) if args.config else default_config
    collection_path = Path(args.collection) if args.collection else default_collection
    debug_title = args.title
    dry_run = args.dry_run

    print("===========================================================")
    print(" Step 3 - Debug test case in collection section")
    print("===========================================================")
    print(f" Script         : {script_path}")
    print(f" Project root   : {project_root}")
    print(f" Config path    : {config_path}")
    print(f" Collection path: {collection_path}")
    print(f" Debug title    : {debug_title!r}")
    print(f" Dry-run        : {dry_run}")
    print("===========================================================\n")

    # 1) Load config & build session
    tcfg = load_config(config_path)
    project_id = tcfg["project_id"]
    suite_id = tcfg.get("suite_id")
    parent_section_id = tcfg.get("parent_section_id")

    print(f"[INFO] project_id        : {project_id}")
    print(f"[INFO] suite_id          : {suite_id}")
    print(f"[INFO] parent_section_id : {parent_section_id}\n")

    session, base_url = build_session(tcfg)

    # 2) Test connection
    test_connection(session, base_url, project_id)

    # 3) Load collection and get its name
    collection_json = load_collection(collection_path)
    collection_name = get_collection_name(collection_json, collection_path)

    print("-----------------------------------------------------------")
    print(" Collection info")
    print("-----------------------------------------------------------")
    print(f"[INFO] Collection name : {collection_name!r}")
    print(f"[INFO] Collection file : {collection_path}")
    print("-----------------------------------------------------------\n")

    # 4) Fetch sections & build index
    sections = fetch_sections(session, base_url, project_id, suite_id)
    sections_idx = build_section_index(sections)

    # 5) Ensure section for collection
    section_id = ensure_collection_section(
        session=session,
        base_url=base_url,
        project_id=project_id,
        suite_id=suite_id,
        parent_section_id=parent_section_id,
        collection_name=collection_name,
        sections_idx=sections_idx,
        dry_run=dry_run,
    )

    if section_id is None and not dry_run:
        print("[ERROR] section_id is None in real mode. Something went wrong.")
        return 1

    if section_id is None and dry_run:
        print("[INFO] No real section ID (dry-run). Skipping debug case creation.")
        print("===========================================================")
        print(" Step 3 - Result")
        print("===========================================================")
        print(f" Collection name : {collection_name!r}")
        print(f" Section ID      : None (would be created in real mode)")
        print(f" Debug title     : {debug_title!r}")
        print(f" Dry-run         : {dry_run}")
        print("===========================================================")
        return 0

    # 6) Fetch existing cases for that section
    cases = fetch_cases_for_section(
        session=session,
        base_url=base_url,
        project_id=project_id,
        section_id=section_id,
        suite_id=suite_id,
    )
    cases_idx = build_case_index(cases)

    # 7) Ensure debug case
    debug_case_id = ensure_debug_case(
        session=session,
        base_url=base_url,
        project_id=project_id,
        section_id=section_id,
        suite_id=suite_id,
        title=debug_title,
        dry_run=dry_run,
        cases_idx=cases_idx,
    )

    print("===========================================================")
    print(" Step 3 - Result")
    print("===========================================================")
    print(f" Collection name : {collection_name!r}")
    print(f" Section ID      : {section_id}")
    print(f" Debug title     : {debug_title!r}")
    print(f" Debug case ID   : {debug_case_id}  (None = would be created in dry-run)")
    print(f" Dry-run         : {dry_run}")
    print("===========================================================")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
