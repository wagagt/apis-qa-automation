#!/usr/bin/env python3
"""Launcher for running Newman from Python (finds newman in PATH or known location)."""

import shutil, subprocess, os, sys
from pathlib import Path

BASE = Path(os.environ.get("USERPROFILE", "")) / "workspace" / "testrail_sync"
COLLECTION = BASE / "newman" / "BPXAPI.postman_collection.json"
ENV = BASE / "newman" / "BPXAPI.postman_environment.json"

# try find newman
newman = shutil.which("newman") or str(Path(os.environ.get("USERPROFILE", "")) / "tools" / "node" / "newman.cmd")
if not Path(newman).exists():
    print("ERROR: newman not found at", newman)
    sys.exit(2)

cmd = [newman, "run", str(COLLECTION), "-e", str(ENV)] + sys.argv[1:]
print("Running:", " ".join(cmd))
rc = subprocess.call(cmd)
sys.exit(rc)
