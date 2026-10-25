#!/usr/bin/env python3
"""Serve the documentation site locally, with the source tree watched.

`mkdocs serve` watches `docs_dir`, which for this repository is `site_src/` —
a generated tree. Editing `README.md` does not change `site_src/`, so the
browser would keep showing the old page.

This script closes that gap. It runs the generator, starts `mkdocs serve`,
and watches the documents the generator reads. A change to any of them
re-runs the generator, which writes `site_src/`, which `mkdocs serve` notices
and rebuilds. The browser reloads.

Usage:

    python scripts/serve_docs.py [--addr 127.0.0.1:8899]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts" / "build_site_docs.py"

# The documents the generator reads. A change to any of them means the
# generated tree is stale.
WATCHED_FILES = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "mkdocs.yml",
    "scripts/build_site_docs.py",
    "mermaid_fences/__init__.py",
]

WATCHED_DIRS = ["essay", "docs", "assets"]


def snapshot() -> dict[str, float]:
    """Modification times of every watched file."""
    stamps: dict[str, float] = {}

    for relative in WATCHED_FILES:
        path = REPO_ROOT / relative
        if path.is_file():
            stamps[relative] = path.stat().st_mtime

    for relative in WATCHED_DIRS:
        root = REPO_ROOT / relative
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                stamps[str(path.relative_to(REPO_ROOT))] = path.stat().st_mtime

    # The application guides are one directory down from the repository root.
    apps = REPO_ROOT / "apps"
    if apps.is_dir():
        for path in apps.glob("*/README.md"):
            stamps[str(path.relative_to(REPO_ROOT))] = path.stat().st_mtime

    return stamps


def run_generator() -> int:
    return subprocess.run(
        [sys.executable, str(GENERATOR), "--clean"],
        cwd=REPO_ROOT,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addr", default="127.0.0.1:8899", help="address to serve on")
    parser.add_argument("--no-serve", action="store_true", help="only run the generator")
    args = parser.parse_args()

    print("assembling site_src/ …", flush=True)
    if run_generator() != 0:
        print("the generator failed; not starting the server", file=sys.stderr)
        return 1

    if args.no_serve:
        return 0

    server = subprocess.Popen(
        [sys.executable, "-m", "mkdocs", "serve", "--dev-addr", args.addr, "--strict"],
        cwd=REPO_ROOT,
    )

    print(f"watching the source documents; the site is at http://{args.addr}/deployflow/", flush=True)
    known = snapshot()

    try:
        while server.poll() is None:
            time.sleep(1.0)
            current = snapshot()
            if current != known:
                known = current
                print("source changed; regenerating site_src/ …", flush=True)
                run_generator()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
