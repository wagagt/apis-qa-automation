#!/usr/bin/env python
#!/usr/bin/env python
"""
apitest_sync_tests.py

Purpose:
- Read config.json (TestRail connection + project/parent section).
- Test connection to TestRail (get_project).
- Read the Postman collection (BPXAPI) to get:
    - Collection name (e.g. "BPXAPI")
    - Folders and requests.

Behavior:
- Ensure ONE collection-level section exists under parent_section_id:
      name = "API-<CollectionName>"  (e.g. "API-BPXAPI")
- Under that section, ensure ONE subsection per Postman folder:
      name = "<folder_name>"         (e.g. "build", "Contact Fleet")
- Under each subsection, ensure ONE TestRail case per request:
      title = "<Request Name>"       (no folder prefix, e.g. "Save a build")

Idempotency:
- Sections and subsections are reused if they already exist (same name + parent).
- Cases are reused if a case with the same title already exists in the subsection.
- New sections/cases are only created when missing.

CLI options:
- --dry-run
    Preview what would be created but do not write changes in TestRail.
- --folder <name>
    Only process requests under folders whose name contains this substring.
- --config / --collection
    Override default paths (config.json and newman/BPXAPI.postman_collection.json).

Typical usage:

    # Preview everything (no writes)
    python apitest_sync_tests.py --dry-run

    # Preview only "Contact Fleet"
    python apitest_sync_tests.py --folder "Contact Fleet" --dry-run

    # Real sync for all folders
    python apitest_sync_tests.py

"""


import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import uuid
import shutil
import csv
from datetime import datetime
import tempfile
import os
from requests import RequestException


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


# Note: automated creation of TestRail custom fields removed. We use a local
# CSV `case_map.csv` to persist mapping automation_id -> testrail_case_id.


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
    collection_section_name: str,
    sections_idx: Dict[Tuple[str, Optional[int]], Dict[str, Any]],
    dry_run: bool,
) -> Optional[int]:
    """
    Ensure there is ONE collection section with:
        name      == collection_section_name (e.g. 'API-BPXAPI')
        parent_id == parent_section_id

    Returns the section_id (or None in dry-run if it would be created).
    """
    key = (collection_section_name, parent_section_id)
    existing = sections_idx.get(key)

    if existing:
        sid = existing.get("id")
        print(
            f"[SECTION] Reusing existing collection section id={sid} "
            f"name={collection_section_name!r} parent={parent_section_id}"
        )
        return sid

    # If not found, create (or simulate)
    if dry_run:
        print(
            f"[DRY-RUN] Would create collection section name={collection_section_name!r} "
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

    payload: Dict[str, Any] = {"name": collection_section_name}
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


def ensure_folder_subsection(
    session: requests.Session,
    base_url: str,
    project_id: int,
    suite_id: Optional[int],
    parent_section_id: Optional[int],
    folder_path: List[str],
    sections_idx: Dict[Tuple[str, Optional[int]], Dict[str, Any]],
    dry_run: bool,
) -> Optional[int]:
    """
    Ensure a hierarchy of subsections exists under parent_section_id, one per
    folder in folder_path. Returns the leaf section_id (or None in dry-run
    if it would be created).
    """
    current_parent = parent_section_id

    if not folder_path:
        # No folder -> use the parent section as target
        return current_parent

    for folder_name in folder_path:
        key = (folder_name, current_parent)
        existing = sections_idx.get(key)

        if existing:
            current_parent = existing.get("id")
            continue

        # Need to create this subsection
        if dry_run:
            print(
                f"[DRY-RUN] Would create subsection name={folder_name!r} "
                f"parent={current_parent}"
            )
            # We cannot know the future id in dry-run; stop here.
            return None

        # Real creation
        if suite_id is not None:
            endpoint = (
                f"{base_url}/index.php?/api/v2/add_section/"
                f"{project_id}&suite_id={suite_id}"
            )
        else:
            endpoint = f"{base_url}/index.php?/api/v2/add_section/{project_id}"

        payload: Dict[str, Any] = {"name": folder_name}
        if current_parent is not None:
            payload["parent_id"] = current_parent

        print("-----------------------------------------------------------")
        print(" Creating folder subsection")
        print("-----------------------------------------------------------")
        print(f"[API] POST {endpoint}")
        print(f"[API] payload={payload}")
        resp = session.post(endpoint, json=payload)
        print(f"[API] Status code: {resp.status_code}")

        if resp.status_code not in (200, 201):
            print("[ERROR] Failed to create folder subsection.")
            print("        Body:", resp.text)
            sys.exit(1)

        s = resp.json()
        sid = s.get("id")
        print(
            f"[OK] Subsection created id={sid} "
            f"name={s.get('name')!r} parent={s.get('parent_id')}"
        )
        print("-----------------------------------------------------------\n")

        # Update index and move down one level
        sections_idx[key] = s
        current_parent = sid

    return current_parent


# ---------- Cases helpers ----------


def fetch_cases_for_section(
    session: requests.Session,
    base_url: str,
    project_id: int,
    section_id: int,
    suite_id: Optional[int],
) -> List[Dict[str, Any]]:
    """Fetch all cases for a given section.

    Handles both:
    - Legacy list response: [ {...}, {...} ]
    - Paginated response with 'cases' key:
      { "offset": 0, "limit": 250, "size": N, "cases": [ {...}, ... ] }
    """
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

    # Newer TestRail responses: dict with "cases" list
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        cases = data["cases"]
    # Older/alternate format: plain list
    elif isinstance(data, list):
        cases = data
    else:
        print("[WARN] Unexpected cases JSON structure, returning empty list.")
        print("       Raw JSON:", data)
        cases = []

    print(f"[INFO] Retrieved {len(cases)} cases for section {section_id}.")
    print("-----------------------------------------------------------\n")
    return cases



def build_case_indexes(cases: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """Build indexes for cases.

    Returns (by_title, by_automation_id_list)
    - by_title: title -> case
    - by_automation_id_list: automation_id -> list[cases] (list to detect collisions)
    """
    by_title: Dict[str, Dict[str, Any]] = {}
    by_aid: Dict[str, List[Dict[str, Any]]] = {}
    for c in cases:
        title = c.get("title")
        if isinstance(title, str):
            by_title[title] = c
        # Attempt to read custom automation id from case payload; common field keys start with 'custom_'
        # Support both direct key and inside 'custom_fields' if present
        aid = None
        # Some TestRail instances include custom fields at top-level
        if isinstance(c, dict):
            # common pattern: c.get('custom_automation_id')
            aid = c.get('custom_automation_id') or c.get('automation_id')
            # also check c.get('custom_fields') mapping
            cf = c.get('custom_fields')
            if not aid and isinstance(cf, dict):
                aid = cf.get('custom_automation_id') or cf.get('automation_id')

        if isinstance(aid, str) and aid:
            by_aid.setdefault(aid, []).append(c)

    return by_title, by_aid


def extract_case_automation_id(case: Dict[str, Any]) -> Optional[str]:
    """Return the automation id for a TestRail case dict if present.

    Handles common patterns:
    - top-level `custom_automation_id` or `automation_id`
    - inside `custom_fields` mapping
    - returns None if not found
    """
    if not isinstance(case, dict):
        return None

    # Check top-level keys first
    for key in ("custom_automation_id", "automation_id"):
        v = case.get(key)
        if isinstance(v, str) and v:
            return v

    # Next, check custom_fields mapping if present
    cf = case.get("custom_fields")
    if isinstance(cf, dict):
        for key in ("custom_automation_id", "automation_id"):
            v = cf.get(key)
            if isinstance(v, str) and v:
                return v

    return None


# -----------------------------
# Postman metadata helpers
# -----------------------------
TESRAIL_META_RE = re.compile(r"\[testrail\](.*?)\[/testrail\]", re.S)


def parse_testrail_meta(description: Optional[str]) -> Dict[str, str]:
    """Parse a [testrail]...[/testrail] block from description into a dict."""
    if not description:
        return {}
    m = TESRAIL_META_RE.search(description)
    if not m:
        return {}
    block = m.group(1).strip()
    out: Dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip()
    return out


def upsert_testrail_meta(description: Optional[str], updates: Dict[str, str]) -> str:
    """Insert or update a [testrail] block in the given description and return the new description."""
    desc = description or ""
    m = TESRAIL_META_RE.search(desc)
    if m:
        meta = parse_testrail_meta(desc)
        meta.update(updates)
        new_block = "\n".join(f"{k}={v}" for k, v in meta.items()) + "\n"
        return TESRAIL_META_RE.sub(f"[testrail]{new_block}[/testrail]", desc)
    else:
        new_block = "[testrail]\n" + "\n".join(f"{k}={v}" for k, v in updates.items()) + "\n[/testrail]"
        if desc and not desc.endswith("\n"):
            desc = desc + "\n\n" + new_block
        else:
            desc = desc + new_block
        return desc


def ensure_request_has_automation_id(item: Dict[str, Any], collection_dirty: Dict[str, bool], dry_run: bool) -> str:
    """Ensure the Postman request item has an automation_id in its description metadata.

    Returns the automation_id (existing or newly generated). Marks collection_dirty['dirty']=True
    when a write would be needed (and not in dry-run).
    """
    # Description may exist at item['request']['description'] or item['description']
    desc = None
    if isinstance(item.get('request'), dict):
        desc = item['request'].get('description')
    if desc is None:
        desc = item.get('description')

    meta = parse_testrail_meta(desc)
    aid = meta.get('automation_id')
    if not aid:
        aid = str(uuid.uuid4())
        if not dry_run:
            new_desc = upsert_testrail_meta(desc, {'automation_id': aid})
            if isinstance(item.get('request'), dict):
                item['request']['description'] = new_desc
            else:
                item['description'] = new_desc
            collection_dirty['dirty'] = True
        else:
            # In dry-run, just indicate we would add it
            collection_dirty['dirty'] = collection_dirty.get('dirty', False) or False
    return aid


def save_collection_with_backup(path: Path, data: Dict[str, Any], dry_run: bool) -> None:
    """Save collection JSON to `path` creating a .bak backup first. Respects dry_run."""
    if dry_run:
        print(f"[DRY-RUN] Would write collection file {path} (no changes saved)")
        return

    bak = path.with_suffix(path.suffix + '.bak')
    try:
        # Use copy to preserve original if write fails
        shutil.copy2(path, bak)
        print(f"[INFO] Backup created: {bak}")
    except Exception:
        # If copy fails (e.g., file doesn't exist yet), ignore but warn
        print(f"[WARN] Could not create backup {bak}; continuing to write file.")

    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] Collection written: {path} (backup: {bak})")


# ---------- Local CSV case map helpers ----------
MAP_PATH = Path(__file__).resolve().parents[1] / "case_map.csv"


def load_case_map() -> Dict[str, Dict[str, str]]:
    """Load the local CSV mapping automation_id -> metadata row.

    Returns a dict keyed by automation_id. Each value is the CSV row dict.
    """
    out: Dict[str, Dict[str, str]] = {}
    if not MAP_PATH.exists():
        return out
    try:
        with MAP_PATH.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                aid = r.get("automation_id")
                if aid:
                    out[aid] = r
    except Exception:
        print(f"[WARN] Could not read case map {MAP_PATH}; starting empty.")
    return out


def append_case_map(automation_id: str, case_id: int, title: str, section_id: Optional[int]) -> None:
    """Update the mapping for `automation_id` and write the CSV atomically.

    This function reads existing map, updates the row for `automation_id`, and
    writes the full CSV to a temporary file then renames it to `case_map.csv` to
    avoid duplicates and provide atomic replace. If an error occurs, the
    previous file is left intact.
    """
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Load existing map
    cur = load_case_map()
    cur[automation_id] = {
        "automation_id": automation_id,
        "testrail_case_id": str(case_id),
        "title": title,
        "section_id": str(section_id) if section_id is not None else "",
        "timestamp": datetime.utcnow().isoformat(),
    }
    save_case_map(cur)


def save_case_map(cur: Dict[str, Dict[str, str]]) -> None:
    """Atomically write the full case map dict to CSV, replacing existing file."""
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="case_map_", suffix=".csv", dir=str(MAP_PATH.parent))
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["automation_id", "testrail_case_id", "title", "section_id", "timestamp"])
            for aid, row in cur.items():
                writer.writerow([
                    row.get("automation_id", ""),
                    row.get("testrail_case_id", ""),
                    row.get("title", ""),
                    row.get("section_id", ""),
                    row.get("timestamp", ""),
                ])
        os.replace(tmp_path, str(MAP_PATH))
    except Exception as e:
        print(f"[WARN] Could not write case map atomically: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


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


def update_case(session: requests.Session, base_url: str, case_id: int, payload: Dict[str, Any], dry_run: bool) -> None:
    """Update an existing case via TestRail API (update_case/{case_id})."""
    if dry_run:
        print(f"[DRY-RUN] Would update case id={case_id} payload={payload}")
        return

    endpoint = f"{base_url}/index.php?/api/v2/update_case/{case_id}"
    print(f"[API] POST {endpoint}")
    print(f"[API] payload={payload}")
    resp = session.post(endpoint, json=payload)
    print(f"[API] Status code: {resp.status_code}")
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Failed to update case {case_id}: {resp.status_code} {resp.text}")


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
            "Step 4: sync all Postman requests as TestRail cases under an "
            "'API-<CollectionName>' section, creating subsections per folder "
            "and using titles '[slug_folder]: Request Name'."
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
    parser.add_argument(
        "--delete-removed",
        action="store_true",
        help="Delete TestRail cases that are present in local CSV but no longer in the Postman collection.",
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
    print(" apitest_sync_tests.py - Sync cases from Postman collection")
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
    collection_section_name = f"API-{collection_name}"

    # Track whether we modified the collection JSON (e.g., added automation_id / case_id)
    collection_dirty: Dict[str, bool] = {"dirty": False}

    print("-----------------------------------------------------------")
    print(" Collection info")
    print("-----------------------------------------------------------")
    print(f"[INFO] Collection name          : {collection_name!r}")
    print(f"[INFO] Collection section name  : {collection_section_name!r}")
    print(f"[INFO] Collection file          : {collection_path}")
    print("-----------------------------------------------------------\n")

    # 4) Ensure collection section `API-<CollectionName>`
    sections = fetch_sections(session, base_url, project_id, suite_id)
    sections_idx = build_section_index(sections)

    collection_section_id = ensure_collection_section(
        session=session,
        base_url=base_url,
        project_id=project_id,
        suite_id=suite_id,
        parent_section_id=parent_section_id,
        collection_section_name=collection_section_name,
        sections_idx=sections_idx,
        dry_run=dry_run,
    )

    # We use a local CSV (case_map.csv) to map Postman automation_id -> TestRail case id.
    # This avoids needing to create custom fields in TestRail and simplifies the
    # reconciliation logic. Load existing map now.
    case_map = load_case_map()

    # Abort early if we couldn't determine/create the collection section
    if collection_section_id is None and not dry_run:
        print("[ERROR] collection_section_id is None in real mode. Aborting to avoid creating cases without a valid section.")
        return 1

    # 6) Walk Postman collection requests
    items = collection_json.get("item") or []
    all_requests = walk_requests(items)

    # Ensure all requests have automation_id and collect the set present in the collection.
    present_automation_ids = set()
    for folder_path, item in all_requests:
        aid = ensure_request_has_automation_id(item, collection_dirty, dry_run)
        if aid:
            present_automation_ids.add(aid)

    # If requested, delete TestRail cases that are mapped but no longer exist in the collection
    if args.delete_removed:
        print("-----------------------------------------------------------")
        print(" Deleting removed tests from TestRail (delete-removed active)")
        print("-----------------------------------------------------------")
        # iterate over a copy of keys to avoid mutation issues
        for aid, row in list(case_map.items()):
            if aid not in present_automation_ids:
                cid = row.get("testrail_case_id")
                if not cid:
                    # no case id to delete
                    case_map.pop(aid, None)
                    continue

                print(f"[INFO] automation_id {aid} mapped to case {cid} is no longer in collection.")
                if dry_run:
                    print(f"[DRY-RUN] Would delete TestRail case id={cid} for automation_id={aid}")
                    # remove from in-memory map for dry-run display purposes
                    case_map.pop(aid, None)
                    continue

                # Perform delete via TestRail API
                try:
                    endpoint = f"{base_url}/index.php?/api/v2/delete_case/{cid}"
                    print(f"[API] POST {endpoint}")
                    resp = session.post(endpoint)
                    print(f"[API] Status code: {resp.status_code}")
                    if resp.status_code in (200, 201, 204):
                        print(f"[OK] Deleted TestRail case id={cid} for automation_id={aid}")
                        case_map.pop(aid, None)
                        # persist updated map
                        try:
                            save_case_map(case_map)
                        except Exception:
                            pass
                    else:
                        print(f"[ERROR] Failed to delete case id={cid}: {resp.status_code} {resp.text}")
                except RequestException as e:
                    print(f"[ERROR] Network error deleting case id={cid}: {e}")


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

        # Ensure each request has a stable automation_id persisted in the request description
        automation_id = ensure_request_has_automation_id(item, collection_dirty, dry_run)

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
        case_title = request_name

        print("-----------------------------------------------------------")
        print(" Request -> Case mapping")
        print("-----------------------------------------------------------")
        print(f"[INFO] Folder path : {folder_path or ['(root)']}")
        print(f"[INFO] Slug        : {slug}")
        print(f"[INFO] Request name: {request_name!r}")
        print(f"[INFO] Case title  : {case_title!r}")
        print(f"[INFO] automation_id: {automation_id}")

        # Determine target section: collection section or folder subsection
        target_section_id: Optional[int]

        if folder_path:
            target_section_id = ensure_folder_subsection(
                session=session,
                base_url=base_url,
                project_id=project_id,
                suite_id=suite_id,
                parent_section_id=collection_section_id,
                folder_path=folder_path,
                sections_idx=sections_idx,
                dry_run=dry_run,
            )
        else:
            target_section_id = collection_section_id

        effective_section_id = target_section_id if target_section_id is not None else -1

        # Check local CSV map for existing mapping by automation_id
        mapped = case_map.get(automation_id)
        if mapped:
            # Validate mapped TestRail case exists before reusing
            case_id_str = mapped.get("testrail_case_id")
            try:
                case_exists = False
                if case_id_str:
                    try:
                        resp = session.get(f"{base_url}/index.php?/api/v2/get_case/{case_id_str}")
                    except RequestException as e:
                        print(f"[WARN] Network error when validating case {case_id_str}: {e}. Will recreate case.")
                        resp = None
                    if resp and resp.status_code == 200:
                        case_exists = True
                if case_exists:
                    # We have the case JSON; check title consistency
                    try:
                        case_json = resp.json()
                        existing_title = case_json.get("title")
                    except Exception:
                        existing_title = None

                    if existing_title is not None and existing_title != case_title:
                        print(f"[INFO] Title mismatch for case {case_id_str}: TestRail title={existing_title!r} -> Postman title={case_title!r}. Updating...")
                        try:
                            update_case(session, base_url, int(case_id_str), {"title": case_title}, dry_run)
                            # update in-memory map and persist if not dry_run
                            case_map[automation_id] = {
                                "automation_id": automation_id,
                                "testrail_case_id": str(case_id_str),
                                "title": case_title,
                                "section_id": mapped.get("section_id", ""),
                                "timestamp": datetime.utcnow().isoformat(),
                            }
                            if not dry_run:
                                save_case_map(case_map)
                            reused_count += 1
                            continue
                        except Exception as e:
                            print(f"[ERROR] Failed to update case title for id={case_id_str}: {e}. Proceeding to recreate case.")
                            # fall through to recreation path

                    # Titles match (or couldn't fetch title) -> reuse
                    # If mapped section differs from target, attempt to move the case
                    mapped_section_raw = mapped.get("section_id")
                    try:
                        mapped_section = int(mapped_section_raw) if mapped_section_raw not in (None, "") else None
                    except Exception:
                        mapped_section = None

                    if target_section_id is not None and mapped_section != target_section_id:
                        print(f"[INFO] Section mismatch for case {case_id_str}: TestRail section={mapped_section} -> target section={target_section_id}. Updating section...")
                        try:
                            update_case(session, base_url, int(case_id_str), {"section_id": target_section_id}, dry_run)
                            # update mapping and persist
                            case_map[automation_id] = {
                                "automation_id": automation_id,
                                "testrail_case_id": str(case_id_str),
                                "title": existing_title if existing_title is not None else case_map.get(automation_id, {}).get("title", ""),
                                "section_id": str(target_section_id),
                                "timestamp": datetime.utcnow().isoformat(),
                            }
                            if not dry_run:
                                save_case_map(case_map)
                            reused_count += 1
                            print(f"[OK] Moved case id={case_id_str} to section={target_section_id}")
                            continue
                        except Exception as e:
                            print(f"[ERROR] Failed to move case id={case_id_str} to section={target_section_id}: {e}. Proceeding without moving.")

                    reused_count += 1
                    print(f"[CASE] Reusing mapped case id={case_id_str} automation_id={automation_id} in section={target_section_id}")
                    continue
                else:
                    print(f"[WARN] Mapped case id={case_id_str} for automation_id={automation_id} not found in TestRail; it will be recreated.")
                    # Remove stale mapping and persist updated map
                    case_map.pop(automation_id, None)
                    try:
                        if not dry_run:
                            save_case_map(case_map)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[WARN] Error validating existing mapping for automation_id={automation_id}: {e}. Proceeding to create case.")

        # Not mapped -> create case and record mapping
        cid = create_case(
            session=session,
            base_url=base_url,
            section_id=effective_section_id,
            title=case_title,
            dry_run=dry_run,
            folder_path=folder_path,
            method=method,
            url_path=url_path,
        )

        if cid is not None and not dry_run:
            created_count += 1
            append_case_map(automation_id, cid, case_title, target_section_id)
            # Update in-memory map so subsequent requests in same run see it
            case_map[automation_id] = {"automation_id": automation_id, "testrail_case_id": str(cid), "title": case_title, "section_id": str(target_section_id)}

    # If we modified the collection (added automation_id or case_id), persist it
    if collection_dirty.get('dirty'):
        if dry_run:
            print("[DRY-RUN] Collection would be updated with new automation_id/case_id entries.")
        else:
            save_collection_with_backup(collection_path, collection_json, dry_run=False)

    print("===========================================================")
    print(" apitest_sync_tests.py - Summary")
    print("===========================================================")
    print(f" Collection name       : {collection_name!r}")
    print(f" Collection section ID : {collection_section_id}")
    print(f" Dry-run               : {dry_run}")
    print(f" Cases reused          : {reused_count}")
    print(f" Cases created         : {created_count}  (0 in dry-run)")
    print(f" Requests skipped      : {skipped_count}  (due to folder filter)")
    print("===========================================================")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
