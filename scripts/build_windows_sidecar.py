from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FFPROBE_STAGE = ROOT / "build" / "ffprobe"
FFMPEG_STAGE = ROOT / "build" / "ffmpeg"
DIST = ROOT / "dist"
TAURI_BINARIES = ROOT / "desktop" / "src-tauri" / "binaries"


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def _require_windows() -> None:
    if platform.system().casefold() != "windows":
        raise RuntimeError("The qualified native sidecar package is built on Windows.")


def _pyinstaller_command() -> list[str]:
    separator = ";"
    return [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        "infomancer-core",
        "--collect-data",
        "certifi",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols",
        "--hidden-import",
        "uvicorn.protocols.http",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.protocols.websockets",
        "--hidden-import",
        "uvicorn.protocols.websockets.auto",
        "--hidden-import",
        "uvicorn.lifespan",
        "--hidden-import",
        "uvicorn.lifespan.on",
        "--add-data",
        f"app/templates{separator}app/templates",
        "--add-data",
        f"app/static{separator}app/static",
        "--add-data",
        f"build/ffprobe/FFPROBE_LICENSE.txt{separator}third-party/ffprobe",
        "--add-data",
        f"build/ffprobe/FFPROBE_NOTICE.txt{separator}third-party/ffprobe",
        "--add-binary",
        f"build/ffprobe/ffprobe.exe{separator}.",
        "--add-data",
        f"build/ffmpeg/FFMPEG_LICENSE.txt{separator}third-party/ffmpeg",
        "--add-data",
        f"build/ffmpeg/FFMPEG_NOTICE.txt{separator}third-party/ffmpeg",
        "--add-binary",
        f"build/ffmpeg/ffmpeg.exe{separator}.",
        "desktop/sidecar.py",
    ]


def build() -> Path:
    _require_windows()
    _run([sys.executable, "scripts/stage_ffprobe.py"])
    _run([sys.executable, "scripts/stage_ffmpeg.py"])
    ffprobe = FFPROBE_STAGE / "ffprobe.exe"
    license_path = FFPROBE_STAGE / "FFPROBE_LICENSE.txt"
    notice_path = FFPROBE_STAGE / "FFPROBE_NOTICE.txt"
    ffmpeg = FFMPEG_STAGE / "ffmpeg.exe"
    ffmpeg_license = FFMPEG_STAGE / "FFMPEG_LICENSE.txt"
    ffmpeg_notice = FFMPEG_STAGE / "FFMPEG_NOTICE.txt"
    for required in (
        ffprobe,
        license_path,
        notice_path,
        ffmpeg,
        ffmpeg_license,
        ffmpeg_notice,
    ):
        if not required.is_file():
            raise RuntimeError(f"Packaging input was not staged: {required}")

    _run(_pyinstaller_command())
    executable = DIST / "infomancer-core.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller did not produce dist/infomancer-core.exe.")

    _run([str(executable), "--check-ffprobe"])
    _run([str(executable), "--check-ffmpeg"])
    host_tuple = subprocess.check_output(
        ["rustc", "--print", "host-tuple"], cwd=ROOT, text=True
    ).strip()
    if not host_tuple:
        raise RuntimeError("rustc did not report a host tuple for the Tauri sidecar.")

    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)
    destination = TAURI_BINARIES / f"infomancer-core-{host_tuple}.exe"
    shutil.copy2(executable, destination)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("The qualified Tauri sidecar was not staged correctly.")
    print(f"Qualified InfoMancer sidecar staged at {destination}", flush=True)
    return destination


def main() -> int:
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
