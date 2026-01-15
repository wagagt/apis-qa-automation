#!/usr/bin/env python
"""
run_sync.py

Interactive CLI runner for synchronizing a Postman collection with TestRail
using the project's virtual environment.

Features:
- Checks that the project venv Python exists.
- Lets you choose between:
    1) Dry-run (preview only, no changes in TestRail)
    2) Real sync (creates/updates sections and test cases)
- Asks which folder to sync (or all folders if left blank).
- Calls sync_newman_to_testrail.py inside the venv, so you don't need to
  "activate" the venv manually in the shell.
"""

import sys
import subprocess
from pathlib import Path
import argparse
import time
from typing import Optional


def build_parser() -> argparse.ArgumentParser:
    """Builds an argument parser for non-interactive runs.

    If no arguments are provided, the script will fall back to interactive mode.
    """
    parser = argparse.ArgumentParser(
        description="CLI runner for sync_newman_to_testrail.py using the project venv."
    )
    parser.add_argument(
        "--mode",
        choices=["dry-run", "sync"],
        help=(
            "Run mode: 'dry-run' (preview) or 'sync' (real). "
            "If omitted, an interactive menu will be shown."
        ),
    )
    parser.add_argument(
        "--folder",
        help=(
            "Name of the Postman collection folder to sync. "
            "If omitted, all folders will be processed."
        ),
    )
    parser.add_argument(
        "--collection",
        help=(
            "Path to the Postman collection JSON. "
            "Default: newman/BPXAPI.postman_collection.json under the project root."
        ),
    )
    parser.add_argument(
        "--config",
        help=(
            "Path to the config.json with TestRail credentials. "
            "Default: config.json under the project root."
        ),
    )
    return parser


def choose_mode_interactive() -> str:
    """Interactive menu to choose between dry-run and real sync."""
    while True:
        print("Select run mode:")
        print("  1) Dry-run (preview only, no changes in TestRail)")
        print("  2) Real sync (create/update in TestRail)")
        print("  3) Exit")
        choice = input("Your choice [1/2/3]: ").strip() or "1"

        if choice == "1":
            return "dry-run"
        elif choice == "2":
            return "sync"
        elif choice == "3":
            print("Exiting.")
            sys.exit(0)
        else:
            print("Invalid choice, please enter 1, 2 or 3.\n")


def ask_folder_interactive() -> Optional[str]:
    """Ask which folder to sync; returns None for 'all folders'."""
    folder = input(
        "Which folder do you want to sync? (leave empty for ALL folders): "
    ).strip()
    return folder or None


def run_with_basic_spinner(cmd: list[str]) -> int:
    """Run the given command, showing a very simple spinner while it runs."""
    spinner = ["|", "/", "-", "\\"]
    spinner_index = 0
    last_tick = time.time()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    print("Running sync script, please wait...\n")

    if proc.stdout is not None:
        for line in proc.stdout:
            print(line, end="")

            now = time.time()
            if now - last_tick > 0.2:
                sys.stdout.write(f"\r[{spinner[spinner_index]}] Sync in progress...")
                sys.stdout.flush()
                spinner_index = (spinner_index + 1) % len(spinner)
                last_tick = now

    proc.wait()
    sys.stdout.write("\r")
    sys.stdout.flush()

    return proc.returncode


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    # Project root = where this script lives
    project_root = Path(__file__).resolve().parent

    # Defaults
    default_collection = project_root / "newman" / "BPXAPI.postman_collection.json"
    default_config = project_root / "config.json"

    # Effective paths (can be overridden via CLI)
    collection = Path(args.collection) if args.collection else default_collection
    config = Path(args.config) if args.config else default_config

    # Use venv Python directly (no shell activation)
    py_env = project_root / "python_scripts" / ".venv" / "Scripts" / "python.exe"
    sync_script = project_root / "python_scripts" / "sync_newman_to_testrail.py"

    print("===========================================================")
    print("  Newman -> TestRail CLI Runner")
    print("===========================================================")
    print(f"  Project root : {project_root}")
    print(f"  Venv Python  : {py_env}")
    print(f"  Sync script  : {sync_script}")
    print(f"  Collection   : {collection}")
    print(f"  Config       : {config}")
    print("===========================================================\n")

    # Basic checks
    if not py_env.exists():
        print("[ERROR] Venv Python not found:")
        print(f"        {py_env}")
        return 1

    if not sync_script.exists():
        print("[ERROR] sync_newman_to_testrail.py not found:")
        print(f"        {sync_script}")
        return 1

    if not collection.exists():
        print("[ERROR] Postman collection not found:")
        print(f"        {collection}")
        return 1

    if not config.exists():
        print("[ERROR] config.json not found:")
        print(f"        {config}")
        return 1

    # Decide mode (interactive if not provided)
    if args.mode is None:
        mode = choose_mode_interactive()
    else:
        mode = args.mode

    # Decide folder (interactive if not provided)
    folder = args.folder
    if folder is None:
        folder = ask_folder_interactive()

    print("\nSummary of this run:")
    print(f"  Mode   : {mode}")
    print(f"  Folder : {folder or '(ALL folders)'}")
    print()

    # Build command for the core script
    cmd = [
        str(py_env),
        str(sync_script),
        "--collection",
        str(collection),
        "--config",
        str(config),
    ]

    if mode == "dry-run":
        cmd.append("--dry-run")

    if folder:
        cmd.extend(["--folder", folder])

    print("Command to be executed:")
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print()

    # Confirm in real sync mode
    if mode == "sync":
        confirm = input(
            "You are about to run a REAL sync (changes in TestRail). "
            "Continue? [y/N]: "
        ).strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted by user.")
            return 0

    # Run with simple spinner
    exit_code = run_with_basic_spinner(cmd)

    print("\n-----------------------------------------------------------")
    if exit_code != 0:
        print(f"[ERROR] sync script exited with code {exit_code}.")
        print("Check the output above for details.")
    else:
        if mode == "dry-run":
            print("[OK] Dry-run completed without errors.")
        else:
            print("[OK] Real sync completed without errors (according to exit code).")
            print("Check TestRail to confirm created/updated sections and cases.")
    print("-----------------------------------------------------------")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
