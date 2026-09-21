from __future__ import annotations

import argparse
import gzip
import hashlib
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
        "archive_sha256": "8883a3dffbd0a16cf4ef95206ea05283f78908dbfb118f73c83f4951dcc06d77",
        "binary_sha256": "04e1307997530f9cf2fe35cba2ca7e8875ca91da02f89d6c7243df819c94ad00",
        "license_sha256": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    },
    ("linux", "x86_64"): {
        "slug": "linux-x64",
        "archive_sha256": "bfe8a8fc511530457b528c48d77b5737527b504a3797a9bc4866aeca69c2dffa",
        "binary_sha256": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
        "license_sha256": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    },
    ("linux", "arm64"): {
        "slug": "linux-arm64",
        "archive_sha256": "754a678672298bc68156adff58aa7385a592c2b30b1d0ae8750c45c915c4bac0",
        "binary_sha256": "6bb182d0d75d23028db82e9e4f723ca69b853d055698486e6984ddb2c06fb8ce",
        "license_sha256": "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903",
    },
    ("darwin", "x86_64"): {
        "slug": "darwin-x64",
        "archive_sha256": "929b375c1182d956c51f7ac25e0b2b0411fb01f6f407aa15c9758efeb4242106",
        "binary_sha256": "ebdddc936f61e14049a2d4b549a412b8a40deeff6540e58a9f2a2da9e6b18894",
        "license_sha256": "2e1d16c72fd74e12063776371da757322f8b77589386532f4fd8634bde7de1af",
    },
    ("darwin", "arm64"): {
        "slug": "darwin-arm64",
        "archive_sha256": "8923876afa8db5585022d7860ec7e589af192f441c56793971276d450ed3bbfa",
        "binary_sha256": "a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584",
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
        raise RuntimeError(f"Unsupported FFmpeg build operating system: {system}")

    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported FFmpeg build architecture: {machine}")
    return os_name, arch


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
