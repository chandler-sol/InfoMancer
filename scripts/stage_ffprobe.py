from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import stat
import urllib.request
from pathlib import Path


VERSION = "6.1.1"
RELEASE_BASE = (
    "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
)

ASSETS = {
    ("windows", "x86_64"): {
        "slug": "win32-x64",
        "archive_sha256": "f309e6223ad89d2fe54bccd420a7709b66fd27540674e92309578ed491a43c8d",
        "binary_sha256": "3a7e2dc003dc2cd1472827e4c7c4f056ae1ae0ae7c5bbc580c99b49827351ba4",
        "license_sha256": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    },
    ("linux", "x86_64"): {
        "slug": "linux-x64",
        "archive_sha256": "25d9b6ccb05e3d9de9e04e31e2506d8dd7f9f0418981965ac6df12e8d3afd067",
        "binary_sha256": "4f231a1960d83e403d08f7971e271707bec278a9ae18e21b8b5b03186668450d",
        "license_sha256": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    },
    ("linux", "arm64"): {
        "slug": "linux-arm64",
        "archive_sha256": "2ab6aba60ee84412dff9188720703376cb4e7aaf7e0b5e43aa8249f2acae5bf8",
        "binary_sha256": "d17ae9b4c297d48e2521ba14e417bb0537c6ff77c584cdbcd6bb0d8d0307a2e8",
        "license_sha256": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    },
    ("darwin", "x86_64"): {
        "slug": "darwin-x64",
        "archive_sha256": "d4da574d6e2e197bd259b47d69cf262df9e312af24ad960444f6d806d3d4c186",
        "binary_sha256": "fa3add0ce901f7241abe0dfc0155d958fc834aca3f8ce61f87cc712ae669c1e0",
        "license_sha256": "2e1d16c72fd74e12063776371da757322f8b77589386532f4fd8634bde7de1af",
    },
    ("darwin", "arm64"): {
        "slug": "darwin-arm64",
        "archive_sha256": "d986a8ec7b030899fe66a8a288ed809a3543338705a3ce178cfb85869c5d80be",
        "binary_sha256": "bb2db6f5d8cef919da12fbf592119a987202a8c060a886f3cab091f9cab90b64",
        "license_sha256": "cb48bf09a11f5fb576cddb0431c8f5ed0a60157a9ec942adffc13907cbe083f2",
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _platform_key() -> tuple[str, str]:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "windows":
        os_name = "windows"
    elif system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    else:
        raise RuntimeError(f"Unsupported FFprobe build operating system: {system}")

    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported FFprobe build architecture: {machine}")
    return os_name, arch


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "InfoMancer-native-packaging/0.8.1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _require_hash(label: str, data: bytes, expected: str) -> None:
    actual = _sha256(data)
    if actual != expected:
        raise RuntimeError(
            f"{label} SHA-256 mismatch: expected {expected}, received {actual}"
        )


def _write_notice(output: Path) -> None:
    (output / "FFPROBE_NOTICE.txt").write_text(
        "InfoMancer native desktop packages include FFprobe from FFmpeg for local "
        "media inspection.\n\n"
        f"Bundled build: FFmpeg/FFprobe {VERSION}\n"
        f"Binary source: {RELEASE_BASE}\n"
        "Upstream project: https://ffmpeg.org/\n\n"
        "The accompanying FFPROBE_LICENSE.txt is the license distributed with "
        "the pinned binary build. Review third-party distribution obligations "
        "before publishing a production release.\n",
        encoding="utf-8",
    )


def _write_build_identity() -> Path:
    commit = (
        os.environ.get("PREVIEW_SHA")
        or os.environ.get("GITHUB_SHA")
        or "local"
    ).strip()
    short_commit = commit[:8] if commit != "local" else "local"
    path = Path("app/static/build-info.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"commit": commit, "short_commit": short_commit},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Stamped InfoMancer runtime build identity: {short_commit}")
    return path


def _reuse_verified_stage(output: Path, key: tuple[str, str], asset: dict) -> Path | None:
    """Reuse a cached stage only after re-verifying the pinned binary and license."""
    binary_name = "ffprobe.exe" if key[0] == "windows" else "ffprobe"
    binary_path = output / binary_name
    license_path = output / "FFPROBE_LICENSE.txt"
    if not binary_path.is_file() or not license_path.is_file():
        return None
    try:
        _require_hash("Cached FFprobe binary", binary_path.read_bytes(), asset["binary_sha256"])
        _require_hash("Cached FFprobe license", license_path.read_bytes(), asset["license_sha256"])
    except (OSError, RuntimeError):
        return None
    if key[0] != "windows":
        binary_path.chmod(
            binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
    _write_notice(output)
    print(f"Reused verified FFprobe {VERSION} at {binary_path}")
    return binary_path


def stage(output: Path) -> Path:
    key = _platform_key()
    asset = ASSETS.get(key)
    if not asset:
        raise RuntimeError(f"No pinned FFprobe asset is configured for {key[0]}/{key[1]}")

    output.mkdir(parents=True, exist_ok=True)
    reused = _reuse_verified_stage(output, key, asset)
    if reused:
        return reused

    slug = asset["slug"]
    archive = _download(f"{RELEASE_BASE}/ffprobe-{slug}.gz")
    _require_hash("FFprobe archive", archive, asset["archive_sha256"])
    binary = gzip.decompress(archive)
    _require_hash("FFprobe binary", binary, asset["binary_sha256"])

    license_text = _download(f"{RELEASE_BASE}/{slug}.LICENSE")
    _require_hash("FFprobe license", license_text, asset["license_sha256"])

    binary_name = "ffprobe.exe" if key[0] == "windows" else "ffprobe"
    binary_path = output / binary_name
    binary_path.write_bytes(binary)
    if key[0] != "windows":
        binary_path.chmod(
            binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    (output / "FFPROBE_LICENSE.txt").write_bytes(license_text)
    _write_notice(output)
    print(f"Staged verified FFprobe {VERSION} at {binary_path}")
    return binary_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage the pinned FFprobe binary used by native InfoMancer packages"
    )
    parser.add_argument("--output", default="build/ffprobe")
    args = parser.parse_args()
    _write_build_identity()
    stage(Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
