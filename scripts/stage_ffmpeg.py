from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import stat
import urllib.request
from pathlib import Path


from app.managed_ffmpeg import (
    FFMPEG_ASSETS as ASSETS,
    FFMPEG_RELEASE_BASE as RELEASE_BASE,
    FFMPEG_VERSION as VERSION,
    ffmpeg_platform_key as _platform_key,
)

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "InfoMancer-native-packaging/0.9"},
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
    (output / "FFMPEG_NOTICE.txt").write_text(
        "InfoMancer native desktop packages include FFmpeg for bounded local "
        "Episode Identity preview-frame extraction.\n\n"
        f"Bundled build: FFmpeg {VERSION}\n"
        f"Binary source: {RELEASE_BASE}\n"
        "Upstream project: https://ffmpeg.org/\n\n"
        "The accompanying FFMPEG_LICENSE.txt is the license distributed with "
        "the pinned binary build. Review third-party distribution obligations "
        "before publishing a production release.\n",
        encoding="utf-8",
    )


def _reuse_verified_stage(output: Path, key: tuple[str, str], asset: dict) -> Path | None:
    binary_name = "ffmpeg.exe" if key[0] == "windows" else "ffmpeg"
    binary_path = output / binary_name
    license_path = output / "FFMPEG_LICENSE.txt"
    if not binary_path.is_file() or not license_path.is_file():
        return None
    try:
        _require_hash("Cached FFmpeg binary", binary_path.read_bytes(), asset["binary_sha256"])
        _require_hash("Cached FFmpeg license", license_path.read_bytes(), asset["license_sha256"])
    except (OSError, RuntimeError):
        return None
    if key[0] != "windows":
        binary_path.chmod(
            binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
    _write_notice(output)
    print(f"Reused verified FFmpeg {VERSION} at {binary_path}")
    return binary_path


def stage(output: Path) -> Path:
    key = _platform_key()
    asset = ASSETS.get(key)
    if not asset:
        raise RuntimeError(f"No pinned FFmpeg asset is configured for {key[0]}/{key[1]}")

    output.mkdir(parents=True, exist_ok=True)
    reused = _reuse_verified_stage(output, key, asset)
    if reused:
        return reused

    slug = asset["slug"]
    archive = _download(f"{RELEASE_BASE}/ffmpeg-{slug}.gz")
    _require_hash("FFmpeg archive", archive, asset["archive_sha256"])
    binary = gzip.decompress(archive)
    _require_hash("FFmpeg binary", binary, asset["binary_sha256"])

    license_text = _download(f"{RELEASE_BASE}/{slug}.LICENSE")
    _require_hash("FFmpeg license", license_text, asset["license_sha256"])

    binary_name = "ffmpeg.exe" if key[0] == "windows" else "ffmpeg"
    binary_path = output / binary_name
    binary_path.write_bytes(binary)
    if key[0] != "windows":
        binary_path.chmod(
            binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    (output / "FFMPEG_LICENSE.txt").write_bytes(license_text)
    _write_notice(output)
    print(f"Staged verified FFmpeg {VERSION} at {binary_path}")
    return binary_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage the pinned FFmpeg binary used by native InfoMancer packages"
    )
    parser.add_argument("--output", default="build/ffmpeg")
    args = parser.parse_args()
    stage(Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
