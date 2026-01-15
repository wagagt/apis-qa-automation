#!/usr/bin/env python3
"""
sync_newman_to_testrail.py
Minimal skeleton to parse a Postman collection and print actions (dry-run mode).
Usage:
  python sync_newman_to_testrail.py --collection ../newman/BPXAPI.postman_collection.json --config ../config.json --dry-run
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any

def parse_args():
    p = argparse.ArgumentParser(description='Sync Postman collection folders -> TestRail sections (minimal).')
    p.add_argument('--collection', required=True, help='Path to Postman collection v2.x JSON')
    p.add_argument('--config', required=True, help='Path to config.json (TestRail credentials & project info)')
    p.add_argument('--dry-run', action='store_true', help='Do not call TestRail API; just print actions')
    return p.parse_args()

def load_json_file(p: Path) -> Any:
    try:
        text = p.read_text(encoding='utf-8')
        return json.loads(text)
    except FileNotFoundError:
        print(f"ERROR: file not found: {p}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON in {p}: {e}", file=sys.stderr)
        sys.exit(3)

def list_folders_and_requests(collection: Dict) -> List[Dict]:
    items = collection.get('item', [])
    folders = []
    for it in items:
        # Folder nodes in Postman collections have an 'item' list inside
        if isinstance(it, dict) and 'item' in it:
            folders.append({
                'name': it.get('name'),
                'requests': [req for req in it.get('item', []) if isinstance(req, dict) and req.get('request')]
            })
    return folders

def pretty_print_plan(coll_name: str, folders: List[Dict], cfg: Dict, dry_run: bool):
    print(f"Collection: {coll_name}")
    print(f"Target TestRail project_id: {cfg.get('testrail', {}).get('project_id')}")
    for f in folders:
        print(f"\n[Folder] {f['name']} -> will map to a TestRail subsection")
        for req in f['requests']:
            title = req.get('name') or (req.get('request', {}).get('url') if req.get('request') else 'unnamed')
            print(f"  - Create test case: {title}")

    if dry_run:
        print("\nDry-run: no API calls performed.")
    else:
        print("\nNOTE: dry-run disabled — no TestRail implementation has been added yet in this skeleton.")

def main():
    args = parse_args()
    coll_path = Path(args.collection)
    cfg_path = Path(args.config)

    coll = load_json_file(coll_path)
    cfg = load_json_file(cfg_path)

    coll_name = coll.get('info', {}).get('name', 'Unknown Collection')
    folders = list_folders_and_requests(coll)

    pretty_print_plan(coll_name, folders, cfg, args.dry_run)

if __name__ == '__main__':
    main()
