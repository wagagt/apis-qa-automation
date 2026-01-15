# -*- coding: utf-8 -*-
"""
sync_get_sections.py
Read nested config.json (structure with "testrail" and "options") and call TestRail.
- GET sections: default action
- Optional: create a section with --create-section "Name" (uses add_section endpoint)

Usage examples:
  python sync_get_sections.py --config ../config.json
  python sync_get_sections.py --config ../config.json --create-section "BPXAPI - prueba"
"""

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

try:
    import requests
    from requests.auth import HTTPBasicAuth
except Exception:
    print("Missing dependency 'requests'. Install with: python -m pip install requests", file=sys.stderr)
    raise SystemExit(2)

LOG = logging.getLogger("testrail_sync")


def load_config(path: str) -> Dict[str, Any]:
    """Load and normalize config file with the expected nested structure."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if "testrail" not in raw or not isinstance(raw["testrail"], dict):
        raise KeyError("config.json must contain a top-level 'testrail' object")

    t = raw["testrail"]
    # required keys
    for k in ("url", "username", "api_key", "project_id"):
        if k not in t:
            raise KeyError(f"Missing '{k}' in config.json -> testrail")

    cfg = {
        "base_url": t["url"].rstrip("/"),
        "user": t["username"],
        "api_key": t["api_key"],
        "project_id": int(t["project_id"]),
        "suite_id": t.get("suite_id", None),
        "options": raw.get("options", {}),
    }
    return cfg


def get_sections(cfg: Dict[str, Any]) -> Any:
    url = f"{cfg['base_url']}/index.php?/api/v2/get_sections/{cfg['project_id']}"
    LOG.info("GET %s", url)
    resp = requests.get(url, auth=HTTPBasicAuth(cfg["user"], cfg["api_key"]), timeout=30)
    resp.raise_for_status()
    return resp.json()


def create_section(cfg: Dict[str, Any], name: str, description: Optional[str] = None, parent_id: int = 0) -> Any:
    url = f"{cfg['base_url']}/index.php?/api/v2/add_section/{cfg['project_id']}"
    payload = {"name": name, "description": description or "", "parent_id": parent_id}
    # If suite_id is provided in config, include it (some TestRail setups require it)
    if cfg.get("suite_id"):
        payload["suite_id"] = cfg["suite_id"]
    LOG.info("POST %s -> %s", url, {"name": name, "parent_id": parent_id, "suite_id": cfg.get("suite_id")})
    resp = requests.post(url, json=payload, auth=HTTPBasicAuth(cfg["user"], cfg["api_key"]), timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    p = argparse.ArgumentParser(description="Get/create TestRail sections using nested config.json")
    p.add_argument("--config", "-c", default="config.json", help="Path to config.json")
    p.add_argument("--create-section", "-C", help="Create a section with this name (optional)")
    p.add_argument("--parent-id", "-p", type=int, default=0, help="Parent section id (for create-section). Default 0")
    p.add_argument("--log", help="Optional log file path")
    p.add_argument("--quiet", action="store_true", help="Only print JSON output (no extra text)")
    args = p.parse_args()

    handlers = [logging.StreamHandler(sys.stderr)]
    if args.log:
        handlers.append(logging.FileHandler(args.log, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(message)s")

    try:
        cfg = load_config(args.config)
    except Exception as e:
        LOG.exception("Cannot load config: %s", e)
        print(f"Error loading config: {e}", file=sys.stderr)
        raise SystemExit(2)

    # NOTE: do not log secrets
    LOG.info("Loaded config for project_id=%s (base_url=%s). options=%s",
             cfg["project_id"], cfg["base_url"], cfg.get("options"))

    try:
        if args.create_section:
            created = create_section(cfg, args.create_section, parent_id=args.parent_id)
            if args.quiet:
                print(json.dumps(created, ensure_ascii=False))
            else:
                print("Section created:")
                print(json.dumps(created, indent=2, ensure_ascii=False))
            return

        # default: list sections
        sections = get_sections(cfg)
        if args.quiet:
            print(json.dumps(sections, ensure_ascii=False))
        else:
            print("\n=== TestRail sections (project_id: {}) ===\n".format(cfg["project_id"]))
            print(json.dumps(sections, indent=2, ensure_ascii=False))
            print("\n=== end ===\n")

    except requests.HTTPError as he:
        LOG.exception("HTTP error: %s", he)
        print("HTTP error:", he, file=sys.stderr)
        if he.response is not None:
            print("Response:", he.response.status_code, file=sys.stderr)
            try:
                print(he.response.text, file=sys.stderr)
            except Exception:
                pass
        raise SystemExit(3)
    except Exception as e:
        LOG.exception("Unexpected error: %s", e)
        print("Error:", e, file=sys.stderr)
        raise SystemExit(4)


if __name__ == "__main__":
    import json  # local import to keep top tidy
    main()
