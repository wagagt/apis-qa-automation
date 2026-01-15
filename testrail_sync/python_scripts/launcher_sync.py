#!/usr/bin/env python3
"""Launcher to run the sync_newman_to_testrail.py using the venv python."""

import subprocess, sys, os
from pathlib import Path

BASE = Path(os.environ.get("USERPROFILE", "")) / "workspace" / "testrail_sync"
PY = BASE / "python_scripts" / ".venv" / "Scripts" / "python.exe"
SCRIPT = BASE / "python_scripts" / "sync_newman_to_testrail.py"
COLLECTION = BASE / "newman" / "BPXAPI.postman_collection.json"
CONFIG = BASE / "config.json"

if not PY.exists():
    print("ERROR: venv python not found at", PY)
    sys.exit(1)

cmd = [str(PY), str(SCRIPT), "--collection", str(COLLECTION), "--config", str(CONFIG)] + sys.argv[1:]
print("Running:", " ".join(cmd))
rc = subprocess.call(cmd)
sys.exit(rc)
