#!/usr/bin/env python
"""
testrail_step4_sync_cases_from_collection.py

Step 4:
- Read config.json
- Test connection to TestRail (get_project)
- Read Postman collection to get its name
- Ensure ONE TestRail section exists with that collection name
  under the configured parent_section_id (if any).
- For each request in the collection, ensure a TestRail case exists
  in that section, with title:

    "[slug_folder_path]: Request Name"

  where:
    - slug_folder_path is derived from the folder hierarchy
      (e.g. "contact-fleet" or "parent-contact-fleet")
    - If the request is at root (no folder), slug_folder_path = "root".

Behavior:
- Section:
    - Reuse if exists (same name + parent_id).
    - Create if not exists (unless --dry-run).
- Cases:
    - Reuse if a case with the same title already exists in that section.
    - Create if not exists (unless --dry-run).

Command examples:

    # Preview all folders (no writes)
    python testrail_step4_sync_cases_from_collection.py --dry-run

    # Preview only "Contact Fleet" folder
    python testrail_step4_sync_cases_from_collection.py --folder "Contact Fleet" --dry-run

    # Real sync for all
    python testrail_step4_sync_cases_from_collection.py

"""

import argparse
import json
import re
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


def build_session(tcfg: Dict[str, Any]) -> Tuple[requests.Session, str]:
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


def create_case(
    session: requests.Session,
    base_url: str,
    section_id: int,
    title: str,
    dry_run: bool,
    folder_path: List[str],
    method: Optional[str],
    url_path: Optional[str],
) -> Optional[int]:
    """Create a new TestRail case (or simulate creation)."""

    if dry_run:
        print(f"[DRY-RUN] Would create case title={title!r} in section={section_id}")
        return None

    endpoint = f"{base_url}/index.php?/api/v2/add_case/{section_id}"

    preconds_lines = []
    if folder_path:
        preconds_lines.append(f"Folder path in Postman: {' / '.join(folder_path)}")
    if url_path:
        preconds_lines.append(f"Endpoint: {url_path}")
    if method:
        preconds_lines.append(f"Method: {method}")

    preconds = "\n".join(preconds_lines) if preconds_lines else None

    payload: Dict[str, Any] = {
        "title": title,
    }
    if preconds:
        # custom_preconds is the default preconditions field in many TestRail configs.
        # If your instance uses a different custom field name, adjust here.
        payload["custom_preconds"] = preconds

    print("-----------------------------------------------------------")
    print(" Creating test case")
    print("-----------------------------------------------------------")
    print(f"[API] POST {endpoint}")
    print(f"[API] payload={payload}")
    resp = session.post(endpoint, json=payload)
    print(f"[API] Status code: {resp.status_code}")

    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to create case.")
        print("        Body:", resp.text)
        sys.exit(1)

    c = resp.json()
    cid = c.get("id")
    print(
        f"[OK] Case created id={cid} "
        f"title={c.get('title')!r} in section={section_id}"
    )
    print("-----------------------------------------------------------\n")

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


def slugify_folder_path(folder_path: List[str]) -> str:
    """
    Build a slug from the folder path.

    Example:
        ["Contact Fleet"] -> "contact-fleet"
        ["Parent", "Contact Fleet"] -> "parent-contact-fleet"
    """
    if not folder_path:
        return "root"

    joined = "-".join(folder_path)  # "Parent-Contact Fleet"
    s = joined.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]+", "", s)
    if not s:
        s = "root"
    return s


def walk_requests(
    items: List[Dict[str, Any]],
    folder_stack: Optional[List[str]] = None,
) -> List[Tuple[List[str], Dict[str, Any]]]:
    """
    Recursively walk Postman items to collect requests.

    Returns a list of tuples: (folder_path, item)
    where:
        folder_path is a list of folder names (may be empty for root),
        item is the request item with 'name' and 'request'.
    """
    if folder_stack is None:
        folder_stack = []

    collected: List[Tuple[List[str], Dict[str, Any]]] = []

    for it in items:
        # Folder
        if "item" in it and isinstance(it["item"], list):
            name = it.get("name") or "Unnamed Folder"
            folder_stack.append(str(name))
            collected.extend(walk_requests(it["item"], folder_stack))
            folder_stack.pop()
        # Request
        elif "request" in it:
            collected.append((list(folder_stack), it))
        # Else: ignore malformed items

    return collected


# ---------- CLI & main ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 4: sync all Postman requests as TestRail cases in the "
            "collection section, using titles '[slug_folder]: Request Name'."
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
        "--folder",
        help=(
            "Optional filter: only process requests under folders whose name "
            "contains this value (case-sensitive simple substring)."
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
    folder_filter = args.folder
    dry_run = args.dry_run

    print("===========================================================")
    print(" Step 4 - Sync cases from Postman collection")
    print("===========================================================")
    print(f" Script         : {script_path}")
    print(f" Project root   : {project_root}")
    print(f" Config path    : {config_path}")
    print(f" Collection path: {collection_path}")
    print(f" Folder filter  : {folder_filter!r}")
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

    # 4) Ensure section for collection
    sections = fetch_sections(session, base_url, project_id, suite_id)
    sections_idx = build_section_index(sections)

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

    # 5) Fetch existing cases in that section
    existing_cases_idx: Dict[str, Dict[str, Any]] = {}
    if section_id is not None:
        cases = fetch_cases_for_section(
            session=session,
            base_url=base_url,
            project_id=project_id,
            section_id=section_id,
            suite_id=suite_id,
        )
        existing_cases_idx = build_case_index(cases)
    else:
        # Dry-run and section not yet created -> we assume no existing cases.
        existing_cases_idx = {}

    # 6) Walk Postman collection requests
    items = collection_json.get("item") or []
    all_requests = walk_requests(items)

    print("-----------------------------------------------------------")
    print(" Postman requests discovered")
    print("-----------------------------------------------------------")
    print(f"[INFO] Total requests found: {len(all_requests)}")
    if folder_filter:
        print(f"[INFO] Folder filter active: {folder_filter!r}")
    print("-----------------------------------------------------------\n")

    created_count = 0
    reused_count = 0
    skipped_count = 0

    for folder_path, item in all_requests:
        # Filter by folder, if requested
        if folder_filter:
            if not any(folder_filter in f for f in folder_path):
                skipped_count += 1
                continue

        request_name = item.get("name") or "Unnamed Request"

        # Determine method & URL path (optional metadata)
        request_obj = item.get("request") or {}
        method = request_obj.get("method")
        url_obj = request_obj.get("url") or {}
        url_path = None

        if isinstance(url_obj, dict):
            # For Postman structure with "path" as list
            path_parts = url_obj.get("path")
            if isinstance(path_parts, list):
                url_path = "/" + "/".join(str(p) for p in path_parts)
            elif isinstance(url_obj.get("raw"), str):
                url_path = url_obj["raw"]

        slug = slugify_folder_path(folder_path)
        case_title = f"[{slug}]: {request_name}"

        print("-----------------------------------------------------------")
        print(" Request -> Case mapping")
        print("-----------------------------------------------------------")
        print(f"[INFO] Folder path : {folder_path or ['(root)']}")
        print(f"[INFO] Slug        : {slug}")
        print(f"[INFO] Request name: {request_name!r}")
        print(f"[INFO] Case title  : {case_title!r}")

        existing_case = existing_cases_idx.get(case_title)
        if existing_case:
            reused_count += 1
            cid = existing_case.get("id")
            print(
                f"[CASE] Reusing existing case id={cid} "
                f"title={case_title!r} in section={section_id}"
            )
            continue

        # If we reached here, case doesn't exist yet -> create (or simulate)
        cid = create_case(
            session=session,
            base_url=base_url,
            section_id=section_id if section_id is not None else -1,
            title=case_title,
            dry_run=dry_run,
            folder_path=folder_path,
            method=method,
            url_path=url_path,
        )

        if cid is not None:
            created_count += 1
            # Update local index so we don't duplicate within same run
            existing_cases_idx[case_title] = {"id": cid, "title": case_title}

    print("===========================================================")
    print(" Step 4 - Summary")
    print("===========================================================")
    print(f" Collection name : {collection_name!r}")
    print(f" Section ID      : {section_id}")
    print(f" Dry-run         : {dry_run}")
    print(f" Cases reused    : {reused_count}")
    print(f" Cases created   : {created_count}  (0 in dry-run)")
    print(f" Requests skipped: {skipped_count}  (due to folder filter)")
    print("===========================================================")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
