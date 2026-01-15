#!/usr/bin/env python3
"""
testrail_tool.py

A small extensible CLI toolbox for TestRail + Postman/Newman utilities.
- Reads config.json (testrail/options/postman).
- Supports interactive menu mode and subcommands mode (for automation).

Usage:
  # Interactive menu:
  python testrail_tool.py --config config.json

  # Subcommands:
  python testrail_tool.py --config config.json projects:list
  python testrail_tool.py --config config.json project:suite-mode
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path


import requests


# -----------------------------
# Config models
# -----------------------------

@dataclass
class TestRailConfig:
    url: str
    username: str
    api_key: str
    project_id: int
    suite_id: Optional[int] = None
    parent_section_id: Optional[int] = None


@dataclass
class AppConfig:
    testrail: TestRailConfig
    options: Dict[str, Any]
    postman: Dict[str, Any]


def load_config(config_path: Path) -> AppConfig:
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    tr = raw.get("testrail")
    if not isinstance(tr, dict):
        raise ValueError("Missing/invalid 'testrail' object in config.json")

    # Allow overriding API key via env var (recommended for security)
    env_api_key = os.getenv("TESTRAIL_API_KEY")
    api_key = env_api_key or tr.get("api_key")
    if not api_key:
        raise ValueError("Missing TestRail api_key (or set TESTRAIL_API_KEY env var).")

    required = ["url", "username", "project_id"]
    for k in required:
        if k not in tr:
            raise ValueError(f"Missing required testrail.{k} in config.json")

    tcfg = TestRailConfig(
        url=str(tr["url"]).rstrip("/"),
        username=str(tr["username"]),
        api_key=str(api_key),
        project_id=int(tr["project_id"]),
        suite_id=None if tr.get("suite_id") in (None, "", "null") else int(tr["suite_id"]),
        parent_section_id=None if tr.get("parent_section_id") in (None, "", "null") else int(tr["parent_section_id"]),
    )

    options = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    postman = raw.get("postman") if isinstance(raw.get("postman"), dict) else {}

    return AppConfig(testrail=tcfg, options=options, postman=postman)


def build_session(tcfg: TestRailConfig) -> requests.Session:
    s = requests.Session()
    s.auth = (tcfg.username, tcfg.api_key)
    # You may add default headers here if needed
    return s


# -----------------------------
# TestRail API helpers
# -----------------------------

def tr_url(base_url: str, endpoint: str) -> str:
    return f"{base_url}/index.php?/api/v2/{endpoint.lstrip('/')}"


def tr_get(session: requests.Session, base_url: str, endpoint: str) -> Any:
    url = tr_url(base_url, endpoint)
    resp = session.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"GET {endpoint} failed ({resp.status_code}): {resp.text}")
    return resp.json()


def suite_mode_label(suite_mode: Optional[int]) -> str:
    return {
        1: "Single Suite",
        2: "Single Suite + Baselines",
        3: "Multiple Suites",
    }.get(suite_mode, f"Unknown ({suite_mode})")


# -----------------------------
# Tool registry (extensible)
# -----------------------------

ToolFn = Callable[[AppConfig, requests.Session], None]

@dataclass
class Tool:
    key: str                 # subcommand key e.g. "projects:list"
    title: str               # menu label
    fn: ToolFn               # function


TOOLS: List[Tool] = []


def register_tool(key: str, title: str) -> Callable[[ToolFn], ToolFn]:
    def decorator(fn: ToolFn) -> ToolFn:
        TOOLS.append(Tool(key=key, title=title, fn=fn))
        return fn
    return decorator


def find_tool(key: str) -> Tool:
    for t in TOOLS:
        if t.key == key:
            return t
    raise KeyError(f"Unknown tool key: {key}")


# Global parsed args (set in main) so tools can access flags like --pretty
GLOBAL_ARGS: Optional[argparse.Namespace] = None


# -----------------------------
# Tools: implementations
# -----------------------------

@register_tool("projects:list", "List Projects (name + id)")
def tool_list_projects(cfg: AppConfig, session: requests.Session) -> None:
    data = tr_get(session, cfg.testrail.url, "get_projects")
    # Support both older API (list) and paginated payloads {projects: [...], size, limit}
    original_payload = data
    if isinstance(data, dict) and isinstance(data.get("projects"), list):
        data = data.get("projects")

    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected get_projects payload: {original_payload!r}")

    # Sort by name for readability
    data_sorted = sorted(data, key=lambda p: str(p.get("name", "")).lower())

    # If user requested pretty JSON, dump the whole original payload nicely.
    if GLOBAL_ARGS is not None and getattr(GLOBAL_ARGS, "pretty", False):
        print(json.dumps(original_payload, indent=2, ensure_ascii=False))
        return

    print("\nProjects:")
    print("-" * 72)
    for p in data_sorted:
        pid = p.get("id")
        name = p.get("name")
        completed = p.get("is_completed")
        print(f"- id={pid:<6} completed={str(completed):<5} name={name}")
    print("-" * 72)
    print(f"Total: {len(data_sorted)}\n")


@register_tool("project:suite-mode", "Check Project Suite Mode (single/baselines/multiple)")
def tool_project_suite_mode(cfg: AppConfig, session: requests.Session) -> None:
    project_id = cfg.testrail.project_id
    proj = tr_get(session, cfg.testrail.url, f"get_project/{project_id}")
    suite_mode = proj.get("suite_mode")

    print("\nProject Suite Mode:")
    print("-" * 72)
    print(f"Project: {proj.get('name')!r} (id={project_id})")
    print(f"suite_mode: {suite_mode} -> {suite_mode_label(suite_mode)}")

    # If multiple suites, list suites and validate configured suite_id if provided
    if suite_mode == 3:
        suites = tr_get(session, cfg.testrail.url, f"get_suites/{project_id}")
        if not isinstance(suites, list):
            raise RuntimeError(f"Unexpected get_suites payload: {suites!r}")

        print("\nSuites:")
        for s in suites:
            sid = s.get("id")
            nm = s.get("name")
            master = s.get("is_master")
            print(f"- id={sid:<6} is_master={str(master):<5} name={nm}")

        if cfg.testrail.suite_id is not None:
            ok = any(int(s.get("id")) == int(cfg.testrail.suite_id) for s in suites if s.get("id") is not None)
            print("\nConfigured suite_id check:")
            print(f"- suite_id in config.json = {cfg.testrail.suite_id} -> {'OK' if ok else 'NOT FOUND'}")
    else:
        if cfg.testrail.suite_id is not None:
            print("\nNote: suite_id is set in config.json, but this project is not Multiple Suites.")
            print("      In Single Suite modes, suite_id is usually unnecessary.")

    print("-" * 72)
    print("")


@register_tool("testrail:whoami", "Test TestRail Connection (get_project)")
def tool_test_connection(cfg: AppConfig, session: requests.Session) -> None:
    project_id = cfg.testrail.project_id
    proj = tr_get(session, cfg.testrail.url, f"get_project/{project_id}")
    print("\nConnection OK:")
    print(f"- Base URL : {cfg.testrail.url}")
    print(f"- User     : {cfg.testrail.username}")
    print(f"- Project  : {proj.get('name')!r} (id={project_id})\n")


# -----------------------------
# Interactive menu
# -----------------------------

def run_menu(cfg: AppConfig, session: requests.Session) -> None:
    while True:
        print("\n=== testrail-tool CLI ===")
        print(f"Project: {cfg.testrail.project_id} | Suite: {cfg.testrail.suite_id} | URL: {cfg.testrail.url}")
        print("")
        for i, t in enumerate(TOOLS, start=1):
            print(f"{i}) {t.title}   [{t.key}]")
        print("q) Quit")
        choice = input("\nSelect an option: ").strip().lower()

        if choice in ("q", "quit", "exit"):
            return

        if not choice.isdigit():
            print("[WARN] Please enter a number or 'q'.")
            continue

        idx = int(choice) - 1
        if idx < 0 or idx >= len(TOOLS):
            print("[WARN] Invalid selection.")
            continue

        tool = TOOLS[idx]
        try:
            tool.fn(cfg, session)
        except Exception as e:
            print(f"[ERROR] Tool failed: {e}")


# -----------------------------
# CLI entry
# -----------------------------

def default_config_path() -> str:
    # config.json one folder above the script location
    script_dir = Path(__file__).resolve().parent
    return str((script_dir / ".." / "config.json").resolve())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="testrail-tool CLI (menu + subcommands)")
    p.add_argument(
        "--config",
        default=default_config_path(),
        help="Path to config.json (default: ../config.json relative to this script)",
    )
    p.add_argument(
        "--no-pretty",
        dest="pretty",
        action="store_false",
        default=True,
        help="Disable pretty-printed JSON output (enabled by default).",
    )
    p.add_argument(
        "command",
        nargs="?",
        help="Optional tool key (e.g. projects:list). If omitted, menu mode starts.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    global GLOBAL_ARGS
    args = build_parser().parse_args(argv)
    GLOBAL_ARGS = args

    cfg = load_config(Path(args.config))
    session = build_session(cfg.testrail)

    if args.command:
        # Non-interactive mode (good for CI)
        try:
            tool = find_tool(args.command)
        except KeyError as e:
            print(f"[ERROR] {e}")
            print("\nAvailable commands:")
            for t in TOOLS:
                print(f"- {t.key}")
            return 2

        try:
            tool.fn(cfg, session)
            return 0
        except Exception as e:
            print(f"[ERROR] Command failed: {e}")
            return 1

    # Interactive menu mode
    run_menu(cfg, session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
