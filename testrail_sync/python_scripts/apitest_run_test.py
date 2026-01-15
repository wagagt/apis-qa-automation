#!/usr/bin/env python
"""
apitest_run_test.py

Creates a TestRail run and pushes results based on a Newman JSON report.

Current behavior (Strategy A: "New Test Run per Execution"):
- Load TestRail config from config.json ("testrail" section).
- Detect the collection section name in TestRail as: "API-<collection_name>".
  * <collection_name> is taken from newman JSON: run.collection.info.name (fallback: "BPXAPI").
- Find that section under parent_section_id (from config).
- Find all child subsections (folders) under that section and all cases inside them.
- Build a mapping: request_name -> case_id (case title must match request name).
- Parse Newman JSON:
  * For each request (item.name), aggregate executions:
    - If any assertion has error => FAILED
    - Else => PASSED
- Create a new TestRail run (add_run) including only the cases we have results for.
- Post results in bulk using add_results_for_cases.

Supports:
- --dry-run: preview (no writes), just logs what would be created/updated.
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


def build_session(tcfg: Dict[str, Any]) -> Tuple[requests.Session, str]:
    """Return (session, base_url)."""
    base_url = tcfg["url"].rstrip("/")
    username = tcfg["username"]
    api_key = tcfg["api_key"]

    session = requests.Session()
    session.auth = (username, api_key)

    return session, base_url


# ---------- TestRail helpers ----------


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


def find_collection_section_id(
    sections: List[Dict[str, Any]],
    collection_section_name: str,
    parent_section_id: Optional[int],
) -> Optional[int]:
    """
    Find the section id where:
      - name      == collection_section_name
      - parent_id == parent_section_id
    """
    for s in sections:
        if s.get("name") == collection_section_name and s.get("parent_id") == parent_section_id:
            return s.get("id")
    return None


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
    elif isinstance(data, dict) and isinstance(data.get("cases"), list):
        cases = data["cases"]
    else:
        print("[WARN] Unexpected cases JSON structure, returning empty list.")
        print("       Raw JSON:", data)
        cases = []

    print(f"[INFO] Retrieved {len(cases)} cases for section {section_id}.")
    print("-----------------------------------------------------------\n")
    return cases

def collect_descendant_section_ids(
    sections: List[Dict[str, Any]],
    root_id: int,
) -> List[int]:
    """
    Return all descendant section IDs under root_id (children, grandchildren, etc).
    """
    children_map: Dict[int, List[int]] = {}

    for s in sections:
        pid = s.get("parent_id")
        sid = s.get("id")
        if isinstance(pid, int) and isinstance(sid, int):
            children_map.setdefault(pid, []).append(sid)

    result: List[int] = []
    stack: List[int] = list(children_map.get(root_id, []))

    while stack:
        sid = stack.pop()
        result.append(sid)
        stack.extend(children_map.get(sid, []))

    return result


def build_case_index_for_collection(
    session: requests.Session,
    base_url: str,
    project_id: int,
    suite_id: Optional[int],
    sections: List[Dict[str, Any]],
    collection_section_id: int,
) -> Dict[str, int]:
    """
    Build a mapping: request_name (case title) -> case_id for all cases
    under the collection section and its direct child subsections.
    """
    case_index: Dict[str, int] = {}

    # 1) Collect ALL descendant subsection IDs under the collection section
    child_sections = collect_descendant_section_ids(sections, collection_section_id)


    print("-----------------------------------------------------------")
    print(" Building case index for collection")
    print("-----------------------------------------------------------")
    print(f"[INFO] Collection section id : {collection_section_id}")
    print(f"[INFO] Descendant subsections ids : {child_sections}")
    print("-----------------------------------------------------------\n")

    # 2) Fetch cases for each subsection
    for sid in child_sections:
        cases = fetch_cases_for_section(session, base_url, project_id, sid, suite_id)
        for c in cases:
            title = c.get("title")
            cid = c.get("id")
            if isinstance(title, str) and isinstance(cid, int):
                case_index[title] = cid

    print("-----------------------------------------------------------")
    print(" Case index summary")
    print("-----------------------------------------------------------")
    print(f"[INFO] Total mapped cases: {len(case_index)}")
    # Optional: print a few for sanity check
    for i, (title, cid) in enumerate(case_index.items()):
        print(f"  - {title!r} -> case_id={cid}")
        if i >= 10:
            print("  ...")
            break
    print("-----------------------------------------------------------\n")

    return case_index


def create_run(
    session: requests.Session,
    base_url: str,
    project_id: int,
    suite_id: Optional[int],
    name: str,
    case_ids: List[int],
    dry_run: bool,
) -> Optional[int]:
    """Create a new TestRail run with the given cases."""
    if not case_ids:
        print("[WARN] No case_ids provided for the run. Nothing to do.")
        return None

    payload: Dict[str, Any] = {
        "name": name,
        "include_all": False,
        "case_ids": case_ids,
    }
    if suite_id is not None:
        payload["suite_id"] = suite_id

    endpoint = f"{base_url}/index.php?/api/v2/add_run/{project_id}"

    print("-----------------------------------------------------------")
    print(" Creating TestRail run")
    print("-----------------------------------------------------------")
    print(f"[INFO] Run name  : {name}")
    print(f"[INFO] case_ids  : {case_ids}")
    print(f"[API]  POST {endpoint}")
    print(f"[API]  payload={payload}")
    if dry_run:
        print("[DRY-RUN] Would create run (no request sent).")
        print("-----------------------------------------------------------\n")
        return None

    resp = session.post(endpoint, json=payload)
    print(f"[INFO] Status code: {resp.status_code}")

    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to create run.")
        print("        Body:", resp.text)
        sys.exit(1)

    data = resp.json()
    run_id = data.get("id")
    print(f"[OK] Run created id={run_id} name={data.get('name')!r}")
    print("-----------------------------------------------------------\n")
    return run_id


def add_results_for_cases(
    session: requests.Session,
    base_url: str,
    run_id: int,
    results: List[Dict[str, Any]],
    dry_run: bool,
) -> None:
    """Post results in bulk using add_results_for_cases."""
    endpoint = f"{base_url}/index.php?/api/v2/add_results_for_cases/{run_id}"

    print("-----------------------------------------------------------")
    print(" Adding results for cases")
    print("-----------------------------------------------------------")
    print(f"[INFO] run_id   : {run_id}")
    print(f"[INFO] #results : {len(results)}")
    if dry_run:
        print("[DRY-RUN] Would call add_results_for_cases with:")
        for r in results:
            print(f"  - case_id={r.get('case_id')} status_id={r.get('status_id')}")
        print("-----------------------------------------------------------\n")
        return

    payload = {"results": results}
    print(f"[API] POST {endpoint}")
    resp = session.post(endpoint, json=payload)
    print(f"[INFO] Status code: {resp.status_code}")

    if resp.status_code not in (200, 201):
        print("[ERROR] Failed to add results for cases.")
        print("        Body:", resp.text)
        sys.exit(1)

    print("[OK] Results posted successfully.")
    print("-----------------------------------------------------------\n")


def fetch_run(session: requests.Session, base_url: str, run_id: int) -> Dict[str, Any]:
    """Validate an existing run exists and is accessible."""
    url = f"{base_url}/index.php?/api/v2/get_run/{run_id}"
    print("-----------------------------------------------------------")
    print(" Validating existing TestRail run")
    print("-----------------------------------------------------------")
    print(f"[INFO] GET {url}")
    resp = session.get(url)
    print(f"[INFO] Status code: {resp.status_code}")

    if resp.status_code != 200:
        print("[ERROR] Could not fetch the run. Please verify run_id and permissions.")
        print("        Body:", resp.text)
        sys.exit(1)

    data = resp.json()
    print(f"[OK] Run found: id={data.get('id')} name={data.get('name')!r}")
    print("-----------------------------------------------------------\n")
    return data


# ---------- Newman helpers ----------


def load_newman_report(path: Path) -> Dict[str, Any]:
    """Load the Newman JSON report."""
    if not path.exists():
        print(f"[ERROR] Newman JSON not found at: {path}")
        sys.exit(1)

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return data


def get_collection_name_from_newman(report: Dict[str, Any]) -> str:
    """
    Try to get the collection name from the Newman report.
    Fallback to 'BPXAPI' if not found.
    """
    col = report.get("collection") or {}
    info = col.get("info") or {}
    name = info.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "BPXAPI"


def aggregate_newman_results(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Aggregate Newman results by request (item.name).

    Returns a dict:
        {
          "Request Name": {
              "status": "passed" | "failed",
              "total_assertions": int,
              "failed_assertions": [ {name, message}, ... ]
          },
          ...
        }

    Strategy:
    - For each execution in report["run"]["executions"]:
        * item_name = execution["item"]["name"]
        * assertions = execution.get("assertions", [])
        * If any assertion["error"] is present => that execution is failed.
    - If any execution for a given item_name fails => overall status "failed".
      Else => "passed".
    """
    run = report.get("run") or {}
    executions = run.get("executions") or []

    aggregated: Dict[str, Dict[str, Any]] = {}

    for ex in executions:
        item = ex.get("item") or {}
        if isinstance(item, dict):
            item_name = item.get("name") or "Unnamed Request"
        else:
            item_name = "Unnamed Request"

        assertions = ex.get("assertions") or []

        failed_here: List[Dict[str, str]] = []
        for a in assertions:
            err = a.get("error")
            if err:
                failed_here.append(
                    {
                        "assertion": str(a.get("assertion")),
                        "message": str(err.get("message", "")),
                    }
                )

        agg = aggregated.setdefault(
            item_name,
            {
                "status": "passed",
                "total_assertions": 0,
                "failed_assertions": [],
            },
        )

        agg["total_assertions"] += len(assertions)
        agg["failed_assertions"].extend(failed_here)

        if failed_here:
            agg["status"] = "failed"

    print("-----------------------------------------------------------")
    print(" Aggregated Newman results")
    print("-----------------------------------------------------------")
    print(f"[INFO] Distinct items with executions: {len(aggregated)}")
    for i, (name, info) in enumerate(aggregated.items()):
        print(
            f"  - {name!r}: status={info['status']} "
            f"assertions={info['total_assertions']} "
            f"failed={len(info['failed_assertions'])}"
        )
        if i >= 10:
            print("  ...")
            break
    print("-----------------------------------------------------------\n")

    return aggregated


# ---------- CLI & main ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a TestRail run and push results based on a Newman JSON report."
        )
    )
    parser.add_argument(
        "--config",
        help="Path to config.json (default: ../config.json from this script).",
    )
    parser.add_argument(
        "--newman-json",
        required=True,
        help="Path to the Newman JSON report.",
    )
    parser.add_argument(
        "--run-name",
        help="Name for the TestRail run. "
             "Default: 'API-<collection_name> - Newman Run'.",
    )
    parser.add_argument(
        "--run-id",
        type=int,
        help=(
            "Existing TestRail run id to update. "
            "If provided, the script will NOT create a new run; it will push results into this run."
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

    config_path = Path(args.config) if args.config else default_config
    newman_path = Path(args.newman_json)
    dry_run = args.dry_run

    print("===========================================================")
    print(" apitest_run_test.py - Create run + push results")
    print("===========================================================")
    print(f" Script       : {script_path}")
    print(f" Project root : {project_root}")
    print(f" Config path  : {config_path}")
    print(f" Newman JSON  : {newman_path}")
    print(f" Dry-run      : {dry_run}")
    print("===========================================================\n")

    # 1) Load config & session
    tcfg = load_config(config_path)
    project_id = tcfg["project_id"]
    suite_id = tcfg.get("suite_id")
    parent_section_id = tcfg.get("parent_section_id")

    print(f"[INFO] project_id        : {project_id}")
    print(f"[INFO] suite_id          : {suite_id}")
    print(f"[INFO] parent_section_id : {parent_section_id}\n")

    session, base_url = build_session(tcfg)
    test_connection(session, base_url, project_id)

    # 2) Load Newman report and get collection name
    report = load_newman_report(newman_path)
    collection_name = get_collection_name_from_newman(report)
    collection_section_name = f"API-{collection_name}"

    print("-----------------------------------------------------------")
    print(" Collection / Section info")
    print("-----------------------------------------------------------")
    print(f"[INFO] Newman collection name   : {collection_name!r}")
    print(f"[INFO] Target section in TR     : {collection_section_name!r}")
    print("-----------------------------------------------------------\n")

    # 3) Find collection section and build case index
    sections = fetch_sections(session, base_url, project_id, suite_id)
    collection_section_id = find_collection_section_id(
        sections, collection_section_name, parent_section_id
    )

    if collection_section_id is None:
        print(
            f"[ERROR] Could not find collection section "
            f"name={collection_section_name!r} parent_id={parent_section_id}."
        )
        print(
            "        Make sure you've run apitest_sync_tests.py at least once "
            "to create the structure."
        )
        return 1

    case_index = build_case_index_for_collection(
        session=session,
        base_url=base_url,
        project_id=project_id,
        suite_id=suite_id,
        sections=sections,
        collection_section_id=collection_section_id,
    )

    if not case_index:
        print("[ERROR] No cases found under the collection section/subsections.")
        print("        Cannot map Newman items to TestRail cases.")
        return 1

    # 4) Aggregate Newman results
    aggregated = aggregate_newman_results(report)

    # 5) Map Newman items -> case_ids + build results payload
    status_map = {"passed": 1, "failed": 5}  # TestRail default statuses
    results_payload: List[Dict[str, Any]] = []
    used_case_ids: List[int] = []

    print("-----------------------------------------------------------")
    print(" Mapping Newman items to TestRail cases")
    print("-----------------------------------------------------------")

    for item_name, info in aggregated.items():
        case_id = case_index.get(item_name)
        if case_id is None:
            print(f"[WARN] No TestRail case found for Newman item {item_name!r}. Skipping.")
            continue

        status_str = info["status"]
        status_id = status_map.get(status_str, 5)

        # Build a short comment
        failed_assertions = info["failed_assertions"]
        comment_lines = [
            f"Status from Newman: {status_str.upper()}",
            f"Total assertions: {info['total_assertions']}",
            f"Failed assertions: {len(failed_assertions)}",
        ]
        if failed_assertions:
            comment_lines.append("")
            comment_lines.append("Failed details:")
            for fa in failed_assertions[:5]:
                comment_lines.append(f"- {fa['assertion']}: {fa['message']}")

        comment = "\n".join(comment_lines)

        results_payload.append(
            {
                "case_id": case_id,
                "status_id": status_id,
                "comment": comment,
            }
        )
        used_case_ids.append(case_id)

        print(
            f"[MAP] item={item_name!r} -> case_id={case_id} "
            f"status={status_str} (status_id={status_id})"
        )

    print("-----------------------------------------------------------")
    print(f"[INFO] Mapped {len(results_payload)} Newman items to TestRail cases.")
    print("-----------------------------------------------------------\n")

    if not results_payload:
        print("[ERROR] No results to send (no Newman items mapped to cases).")
        return 1

    # 6) Decide whether to create a new run or update an existing one
    target_run_id: Optional[int] = None

    # Define run_name for logging purposes (even in update mode)
    run_name = args.run_name or f"{collection_section_name} - Newman Run"

    if args.run_id:
        # Update existing run
        target_run_id = int(args.run_id)
        print(f"[INFO] Update mode enabled. Using existing run_id={target_run_id}.")
        fetch_run(session, base_url, target_run_id)  # validate it exists / accessible
    else:
        # Create new run
        target_run_id = create_run(
            session=session,
            base_url=base_url,
            project_id=project_id,
            suite_id=suite_id,
            name=run_name,
            case_ids=sorted(set(used_case_ids)),
            dry_run=dry_run,
        )

    if dry_run:
        print("[DRY-RUN] Skipping add_results_for_cases because dry-run is enabled.")
        print("===========================================================")
        print(" apitest_run_test.py - Summary (DRY-RUN)")
        print("===========================================================")
        print(f" Collection name       : {collection_name!r}")
        print(f" Collection section ID : {collection_section_id}")
        if args.run_id:
            print(f" Run id (target)       : {target_run_id} (update existing)")
        else:
            print(f" Run name (planned)    : {run_name!r} (create new)")
        print(f" Results to send       : {len(results_payload)}")
        print("===========================================================")
        return 0

    if target_run_id is None:
        print("[ERROR] No target run_id available. Aborting.")
        return 1

    add_results_for_cases(
        session=session,
        base_url=base_url,
        run_id=target_run_id,
        results=results_payload,
        dry_run=False,
    )

    print("===========================================================")
    print(" apitest_run_test.py - Summary")
    print("===========================================================")
    print(f" Collection name       : {collection_name!r}")
    print(f" Collection section ID : {collection_section_id}")
    print(f" Run id                : {target_run_id}")
    print(f" Results posted        : {len(results_payload)}")
    print("===========================================================")

    return 0



if __name__ == "__main__":
    raise SystemExit(main())
