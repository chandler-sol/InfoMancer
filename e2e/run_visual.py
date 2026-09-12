from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = E2E_DIR.parent
DEFAULT_RUNTIME = E2E_DIR / ".e2e-runtime"
BOOTSTRAP_TOKEN = "e2e-library-card-123456"
SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def wait_for_health(port: int, process: subprocess.Popen, log_path: Path) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"InfoMancer acceptance server on port {port} exited before it became ready. "
                f"See {log_path}."
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    print(f"Ready: {url}")
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(
        f"InfoMancer acceptance server on port {port} did not become ready within 30 seconds. "
        f"See {log_path}."
    )


def start_server(
    *, port: int, database: Path, media_root: Path, log_path: Path,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.Popen, object]:
    env = os.environ.copy()
    env.update({
        "INFOMANCER_DATABASE": str(database),
        "INFOMANCER_SECRET": SECRET,
        "MEDIA_BROWSE_ROOTS": str(media_root),
    })
    env.update(extra_env or {})
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return process, log_handle


def stop_servers(processes: list[tuple[subprocess.Popen, object]]) -> None:
    for process, _log_handle in processes:
        if process.poll() is None:
            process.terminate()
    for process, log_handle in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            log_handle.close()


def playwright_executable() -> Path:
    name = "playwright.cmd" if os.name == "nt" else "playwright"
    executable = E2E_DIR / "node_modules" / ".bin" / name
    if not executable.is_file():
        raise RuntimeError(
            "Playwright is not installed in e2e/node_modules. Run `npm install` in the e2e folder, "
            "then run `npx playwright install chromium` once before starting the visual test runner."
        )
    return executable


def prepare_runtime(runtime: Path) -> None:
    env = os.environ.copy()
    env["INFOMANCER_E2E_ROOT"] = str(runtime)
    subprocess.run(
        [sys.executable, str(E2E_DIR / "create_fixtures.py")],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def run(mode: str, passthrough: list[str]) -> int:
    runtime = Path(os.environ.get("INFOMANCER_E2E_ROOT", DEFAULT_RUNTIME)).resolve()
    prepare_runtime(runtime)

    media_root = runtime / "media" / "Movies"
    state = runtime / "state"
    logs = runtime / "logs"
    processes: list[tuple[subprocess.Popen, object]] = []

    server_definitions = (
        (8787, state / "token.db", {"INFOMANCER_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN}, "token-server.log"),
        (8788, state / "sandbox.db", {"INFOMANCER_SANDBOX": "1"}, "sandbox-server.log"),
        (8789, state / "tour.db", {"INFOMANCER_SANDBOX": "1"}, "tour-server.log"),
    )

    try:
        for port, database, extra_env, log_name in server_definitions:
            log_path = logs / log_name
            process, log_handle = start_server(
                port=port,
                database=database,
                media_root=media_root,
                log_path=log_path,
                extra_env=extra_env,
            )
            processes.append((process, log_handle))
            wait_for_health(port, process, log_path)

        env = os.environ.copy()
        env.update({
            "INFOMANCER_E2E_TOKEN_URL": "http://127.0.0.1:8787",
            "INFOMANCER_E2E_SANDBOX_URL": "http://127.0.0.1:8788",
            "INFOMANCER_E2E_TOUR_URL": "http://127.0.0.1:8789",
            "INFOMANCER_E2E_DATABASE": str(state / "sandbox.db"),
            "INFOMANCER_E2E_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
        })

        command = [str(playwright_executable()), "test"]
        if mode == "watch":
            command.append("--headed")
        elif mode == "ui":
            command.append("--ui")
        elif mode == "debug":
            command.append("--debug")
        elif mode != "headless":
            raise ValueError(f"Unknown test mode: {mode}")
        command.extend(passthrough)

        print(f"\nInfoMancer visual test runtime: {runtime}")
        print(f"Playwright mode: {mode}\n")
        return subprocess.call(command, cwd=E2E_DIR, env=env)
    except KeyboardInterrupt:
        return 130
    finally:
        stop_servers(processes)
        print(f"Acceptance servers stopped. Logs remain in {logs}.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create disposable InfoMancer E2E fixtures, start acceptance servers, and run Playwright."
    )
    parser.add_argument("mode", choices=("headless", "watch", "ui", "debug"), nargs="?", default="watch")
    parser.add_argument(
        "playwright_args", nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to `playwright test` (prefix with -- when using npm).",
    )
    args = parser.parse_args()
    passthrough = args.playwright_args
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    try:
        return run(args.mode, passthrough)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Visual test runner could not start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
