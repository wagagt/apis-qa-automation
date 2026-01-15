#!/usr/bin/env python3
"""
create_section.py
Prueba rápida para confirmar que tu cuenta puede GET sections y POST add_section.
Lee credenciales desde config.json.
"""

import argparse
import json
import logging
import sys
from base64 import b64encode

import requests

LOG = logging.getLogger("testrail-test")

def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def make_auth_header(username, api_key):
    pair = f"{username}:{api_key}"
    token = b64encode(pair.encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

def get_sections(cfg):
    base = cfg["testrail"]["url"].rstrip("/")
    proj = cfg["testrail"]["project_id"]
    url = f"{base}/index.php?/api/v2/get_sections/{proj}"
    LOG.info("GET %s", url)
    h = make_auth_header(cfg["testrail"]["username"], cfg["testrail"]["api_key"])
    r = requests.get(url, headers=h, timeout=30)
    LOG.info("Response: %s %s", r.status_code, r.text[:400])
    r.raise_for_status()
    data = r.json()
    # Normaliza: si la respuesta contiene la clave 'sections' devolvemos esa lista,
    # si la API devolvió directamente una lista, la devolvemos tal cual.
    if isinstance(data, dict) and "sections" in data:
        return data["sections"]
    if isinstance(data, list):
        return data
    # fallback: intentar extraer cualquier valor iterable
    return []

def add_section(cfg, name, description="", parent_id=0):
    base = cfg["testrail"]["url"].rstrip("/")
    proj = cfg["testrail"]["project_id"]
    url = f"{base}/index.php?/api/v2/add_section/{proj}"
    payload = {"name": name, "description": description}
    # include suite_id if provided
    if cfg["testrail"].get("suite_id"):
        payload["suite_id"] = cfg["testrail"]["suite_id"]
    if parent_id:
        payload["parent_id"] = int(parent_id)
    LOG.info("POST %s -> %s", url, payload)
    h = make_auth_header(cfg["testrail"]["username"], cfg["testrail"]["api_key"])
    r = requests.post(url, headers=h, json=payload, timeout=30)
    LOG.info("Response: %s %s", r.status_code, r.text[:800])
    r.raise_for_status()
    return r.json()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="../config.json", help="Path to config.json")
    p.add_argument("--name", required=True, help="Section name to create")
    p.add_argument("--description", default="Test- created from newman sync script- ", help="Section description")
    p.add_argument("--parent-id", type=int, default=0, help="Parent section id (optional)")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")

    try:
        cfg = load_config(args.config)
    except Exception as e:
        LOG.error("Error leyendo config: %s", e)
        sys.exit(1)

    try:
        print("== GET existing sections (short list) ==")
        secs = get_sections(cfg)
        print(f"Found {len(secs)} sections (showing up to 8):")
        for s in secs[:8]:
            print(f" - id={s.get('id')} name={s.get('name')} parent_id={s.get('parent_id')} suite_id={s.get('suite_id')}")
    except requests.HTTPError as he:
        LOG.error("GET sections failed: %s", he)
        print("GET sections failed. Check credentials and config.json.")
        sys.exit(2)

    try:
        print("\n== Creating section now ==")
        created = add_section(cfg, args.name, args.description, args.parent_id)
        print("Created section:")
        print(json.dumps(created, indent=2, ensure_ascii=False))
        print("\n✅ Si ves el JSON con id -> la creación fue exitosa. Verifica en TestRail UI (sección / suite correspondientes).")
    except requests.HTTPError as he:
        LOG.error("POST add_section failed: %s", he)
        text = he.response.text if hasattr(he, "response") and he.response is not None else ""
        print("Create section failed. Response:", getattr(he, "response", None) and he.response.status_code, text)
        sys.exit(3)

if __name__ == "__main__":
    main()



# execute
# & "$env:USERPROFILE\workspace\testrail_sync\python_scripts\.venv\Scripts\python.exe" `
#   "$env:USERPROFILE\workspace\testrail_sync\python_scripts\create_section.py" `
#   --config "$env:USERPROFILE\workspace\testrail_sync\config.json" `
#   --name "BPXAPI - prueba creación directa" `
#   --description "Sección creada via create_section.py" `
#   --parent-id 8990
