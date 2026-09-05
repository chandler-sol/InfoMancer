from __future__ import annotations

import argparse
import os
import shutil
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

DESKTOP_VERSION = "0.8.1-beta.1"


def _root_is_accessible(root: Path) -> bool:
    """Return whether a browse root can actually be opened by this process."""
    try:
        with os.scandir(root):
            return True
    except OSError:
        return False


def _windows_drive_strings_from_mask(mask: int) -> list[str]:
    return [
        f"{letter}:\\"
        for index, letter in enumerate(string.ascii_uppercase)
        if mask & (1 << index)
    ]


def _windows_logical_drives() -> list[Path]:
    """Enumerate Windows drive letters without probing their filesystems.

    Mapped SMB/NFS drives can exist in the user's Windows session while a live
    filesystem probe is slow, disconnected, or temporarily denied. Asking the
    Win32 drive table first keeps those locations visible to InfoMancer and
    leaves accessibility checks to the folder browser.
    """
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    except (AttributeError, OSError, TypeError, ValueError):
        return []
    return [Path(value) for value in _windows_drive_strings_from_mask(mask)]


def _dedupe_media_roots(roots: list[Path]) -> list[Path]:
    """Deduplicate roots without resolving them through the filesystem."""
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(os.fspath(root))).casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _default_media_roots() -> list[Path]:
    roots: list[Path] = []
    home = Path.home()
    if _root_is_accessible(home):
        roots.append(home)
    if os.name == "nt":
        roots.extend(_windows_logical_drives())
    elif sys.platform == "darwin":
        volumes = Path("/Volumes")
        if _root_is_accessible(volumes):
            roots.append(volumes)
    else:
        for candidate in (Path("/media"), Path("/mnt")):
            if _root_is_accessible(candidate):
                roots.append(candidate)
    return _dedupe_media_roots(roots)


def _inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class _TeeStream:
    """Mirror a captured runtime stream into the persistent desktop core log."""

    def __init__(self, primary, log_stream) -> None:
        self.primary = primary
        self.log_stream = log_stream

    @property
    def encoding(self):
        return getattr(self.primary, "encoding", None) or "utf-8"

    def write(self, text) -> int:
        value = str(text)
        if self.primary is not None:
            try:
                self.primary.write(value)
            except Exception:
                pass
        try:
            self.log_stream.write(value)
        except Exception:
            pass
        return len(value)

    def flush(self) -> None:
        for stream in (self.primary, self.log_stream):
            if stream is None:
                continue
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self.primary and self.primary.isatty())
        except Exception:
            return False


def _ensure_runtime_streams(data_dir: Path) -> None:
    """Persist Windows core output even when Tauri captures stdout/stderr."""
    if os.name != "nt":
        return
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = (log_dir / "desktop-core.log").open(
        "a", encoding="utf-8", buffering=1
    )
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _TeeStream(original_stdout, stream) if original_stdout is not None else stream
    sys.stderr = _TeeStream(original_stderr, stream) if original_stderr is not None else stream
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] InfoMancer desktop core diagnostics started.",
        flush=True,
    )


def _process_is_alive(pid: int) -> bool:
    """Return whether a process still exists without requiring extra packages."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            open_process.restype = ctypes.c_void_p
            get_exit_code = kernel32.GetExitCodeProcess
            get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_exit_code.restype = ctypes.c_int
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [ctypes.c_void_p]
            close_handle.restype = ctypes.c_int
            handle = open_process(process_query_limited_information, 0, pid)
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                exit_code = ctypes.c_uint32()
                if not get_exit_code(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == still_active
            finally:
                close_handle(handle)
        except (AttributeError, OSError, TypeError, ValueError):
            return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _start_onefile_parent_watchdog() -> None:
    """Stop the real Windows one-file worker if its PyInstaller parent is killed.

    PyInstaller one-file builds run a bootloader parent plus the Python application
    child. Tauri owns the bootloader PID. On Windows, terminating that parent can
    leave the Python child running in the background. That orphan keeps the SQLite
    catalog and runtime lease alive, which prevents the next desktop launch.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    parent_pid = os.getppid()
    if parent_pid <= 0:
        return

    def watch() -> None:
        while True:
            time.sleep(0.05)
            if not _process_is_alive(parent_pid):
                os._exit(0)

    threading.Thread(
        target=watch,
        name="infomancer-desktop-parent-watchdog",
        daemon=True,
    ).start()


def create_recovery_package(data_dir: Path, output: Path) -> None:
    data_dir = data_dir.expanduser().resolve()
    database = data_dir / "infomancer.db"
    if not database.is_file():
        raise RuntimeError("No local InfoMancer database was found to back up.")

    output = output.expanduser().resolve()
    if _inside(output, data_dir):
        raise RuntimeError(
            "Choose a recovery destination outside InfoMancer's application-data folder."
        )
    if output.suffix.casefold() != ".infomancer-backup":
        output = output.with_name(output.name + ".infomancer-backup")
    output.parent.mkdir(parents=True, exist_ok=True)

    from app.recovery_package import RecoveryPackageService

    service = RecoveryPackageService(database, DESKTOP_VERSION)
    generated = service.create()
    try:
        shutil.copy2(generated, output)
        service.verify(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    print(str(output), flush=True)


def _check_ffprobe() -> int:
    """Verify the packaged core can find and execute its media inspector."""
    from app.media_info import ffprobe_executable

    try:
        result = subprocess.run(
            [ffprobe_executable(), "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 23
    return 0 if result.returncode == 0 else 23


def main() -> int:
    parser = argparse.ArgumentParser(description="InfoMancer Desktop local core")
    parser.add_argument("--port", type=int)
    # Retained for backwards-compatible manual launches. The native launcher sends
    # this secret through the inherited environment so it is not exposed in process
    # command-line listings.
    parser.add_argument("--bootstrap-token", default="")
    parser.add_argument("--data-dir")
    parser.add_argument("--recovery-output")
    parser.add_argument("--check-ffprobe", action="store_true")
    args = parser.parse_args()

    if args.check_ffprobe:
        return _check_ffprobe()
    if not args.data_dir:
        parser.error("--data-dir is required unless --check-ffprobe is used")

    data_dir = Path(args.data_dir).expanduser().resolve()
    _ensure_runtime_streams(data_dir)
    _start_onefile_parent_watchdog()
    if args.recovery_output:
        try:
            create_recovery_package(data_dir, Path(args.recovery_output))
        except Exception as exc:
            print(f"Recovery package failed: {exc}", file=sys.stderr, flush=True)
            return 2
        return 0

    if args.port is None:
        parser.error("--port is required when starting the local InfoMancer core")

    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / "infomancer.db"
    bootstrap_token = (
        args.bootstrap_token or os.getenv("INFOMANCER_BOOTSTRAP_TOKEN", "")
    ).strip()

    os.environ["INFOMANCER_DATABASE"] = str(database)
    os.environ["INFOMANCER_AUTH_MODE"] = "local"
    os.environ["INFOMANCER_COOKIE_SECURE"] = "false"
    os.environ["INFOMANCER_PUBLIC_URL"] = f"http://127.0.0.1:{args.port}"
    os.environ["INFOMANCER_TRUSTED_HOSTS"] = "127.0.0.1,localhost"
    os.environ["INFOMANCER_TRUST_CLOUDFLARE_PROXY"] = "false"
    os.environ["INFOMANCER_RUNTIME_CONTEXT"] = "desktop"
    if bootstrap_token:
        os.environ["INFOMANCER_BOOTSTRAP_TOKEN"] = bootstrap_token
    else:
        os.environ.pop("INFOMANCER_BOOTSTRAP_TOKEN", None)
    if not os.getenv("MEDIA_BROWSE_ROOTS", "").strip():
        os.environ["MEDIA_BROWSE_ROOTS"] = ",".join(str(path) for path in _default_media_roots())

    from app.main import app
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
