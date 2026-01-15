#!/usr/bin/env python
"""
apitest_cli.py

Main CLI entry point to orchestrate:
- Syncing TestRail structure from a Postman collection (sections + cases).
- (Planned) Running Newman + pushing results to TestRail (runs + results).

Current features:
1) Sync TestRail structure (sections + cases)
   - Calls: python_scripts/apitest_sync_tests.py
   - Allows dry-run vs real sync.

2) Run collection and push results
   - Placeholder for now (will call apitest_run_test.py in a next iteration).

Usage (from project root):

    cd C:\\Users\\wgonzal6\\workspace\\testrail_sync
    python .\\python_scripts\\apitest_cli.py
"""

import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


def run_subprocess(cmd: List[str]) -> int:
    """
    Run a subprocess, streaming stdout/stderr to the console.
    Returns the process' exit code.
    """
    print("\n[CLI] Executing command:")
    print("     " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print("")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if proc.stdout is not None:
        for line in proc.stdout:
            print(line, end="")

    proc.wait()
    return proc.returncode


def ensure_paths() -> Tuple[Path, Path, Path]:
    """
    Resolve and validate:
    - project_root      -> repo root (parent of python_scripts)
    - script_dir        -> python_scripts
    - venv_python       -> python_scripts/.venv/Scripts/python.exe
    """
    script_path = Path(__file__).resolve()
    script_dir = script_path.parent              # .../testrail_sync/python_scripts
    project_root = script_dir.parent             # .../testrail_sync
    venv_python = script_dir / ".venv" / "Scripts" / "python.exe"

    if not venv_python.exists():
        print("[ERROR] Venv python not found:")
        print(f"        {venv_python}")
        print("Please create/verify the virtualenv under python_scripts/.venv")
        print("Example:")
        print("    cd python_scripts")
        print("    python -m venv .venv")
        return project_root, script_dir, venv_python

    return project_root, script_dir, venv_python


def menu() -> str:
    """Display the CLI menu and return the selected option."""
    print("===========================================================")
    print(" API Test CLI")
    print("===========================================================")
    print(" 1) Sync TestRail structure (sections + cases)")
    print(" 2) Run collection and push results to TestRail (COMING SOON)")
    print(" 3) Exit")
    choice = input("Choose an option [1/2/3]: ").strip() or "1"
    return choice


def handle_sync_tests(project_root: Path, script_dir: Path, venv_python: Path) -> None:
    """
    Handle option 1:
    - Ask for dry-run (preview) vs real sync.
    - Call apitest_sync_tests.py with the proper flags.
    """
    sync_script = script_dir / "apitest_sync_tests.py"

    if not sync_script.exists():
        print("[ERROR] apitest_sync_tests.py not found at:")
        print(f"        {sync_script}")
        return

    print("\n[Sync Tests] Do you want to run a dry-run (no changes) or real sync?")
    print("  d) Dry-run (preview only)")
    print("  r) Real sync (write changes to TestRail)")
    mode = input("Choose mode [d/r] (default: d): ").strip().lower() or "d"

    cmd = [str(venv_python), str(sync_script)]

    if mode == "d":
        cmd.append("--dry-run")
        print("\n[Sync Tests] Running DRY-RUN (no changes will be written)...")
    else:
        print("\n[Sync Tests] Running REAL sync (changes will be written)...")
        confirm = input("Confirm real sync? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            print("[Sync Tests] Aborted by user.")
            return

    exit_code = run_subprocess(cmd)

    if exit_code != 0:
        print(f"\n[Sync Tests] Finished with errors (exit code {exit_code}).")
    else:
        print("\n[Sync Tests] Finished successfully.")


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    project_root, script_dir, venv_python = ensure_paths()

    if not venv_python.exists():
        # Already printed an error in ensure_paths()
        return 1

    while True:
        choice = menu()

        if choice == "1":
            handle_sync_tests(project_root, script_dir, venv_python)
        elif choice == "2":
            print("\n[TODO] Option 2 (Run collection and push results) is not implemented yet.")
            print("       It will call apitest_run_test.py in a next iteration.\n")
        elif choice == "3":
            print("Exiting.")
            return 0
        else:
            print("\nInvalid option. Please choose 1, 2 or 3.\n")


if __name__ == "__main__":
    raise SystemExit(main())
