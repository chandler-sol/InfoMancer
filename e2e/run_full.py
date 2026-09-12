from __future__ import annotations

import argparse
import subprocess
import sys

from run_visual import REPO_ROOT, run as run_visual


def run_command(command: list[str]) -> int:
    print(f"\n> {' '.join(command)}\n")
    return subprocess.call(command, cwd=REPO_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the complete local InfoMancer qualification suite, then the full browser acceptance pass."
    )
    parser.add_argument("mode", choices=("headless", "watch", "ui", "debug"), nargs="?", default="watch")
    args = parser.parse_args()

    print("InfoMancer full local qualification")
    print("Phase 1/3: Python regression suite")
    result = run_command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    if result:
        return result

    print("Phase 2/3: Compile application")
    result = run_command([sys.executable, "-m", "compileall", "-q", "app"])
    if result:
        return result

    print("Phase 3/3: Full Playwright browser acceptance")
    return run_visual(args.mode, [])


if __name__ == "__main__":
    raise SystemExit(main())
