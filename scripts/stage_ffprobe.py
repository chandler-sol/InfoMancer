from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import stat
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


VERSION = "n9.0.1-29-gad500d59cb"
FFMPEG_COMMIT = "ad500d59cb6e0126add4fcb95afb4e2557c4292c"
BTBN_RELEASE = "autobuild-2026-09-11-13-20"
BTBN_COMMIT = "cc8f0958be119db774cdaf6c50065651a4901e72"
RELEASE_BASE = (
    f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{BTBN_RELEASE}"
)
FFMPEG_SOURCE_URL = f"https://github.com/FFmpeg/FFmpeg/archive/{FFMPEG_COMMIT}.tar.gz"
BTBN_SOURCE_URL = f"https://github.com/BtbN/FFmpeg-Builds/archive/{BTBN_COMMIT}.tar.gz"
LICENSE_URL = (
    "https://raw.githubusercontent.com/FFmpeg/FFmpeg/"
    f"{FFMPEG_COMMIT}/COPYING.LGPLv3"
)
LICENSE_GIT_BLOB_SHA1 = "65c5ca88a67c30becee01c5a8816d964b03862f9"

# Only explicitly reviewed LGPL builds are allowed here. macOS is intentionally
# absent until an equally reviewable LGPL build/source path is added.
ASSETS = {
    ("windows", "x86_64"): {
        "filename": "ffmpeg-n9.0.1-29-gad500d59cb-win64-lgpl-9.0.zip",
        "archive_sha256": "2ed9c183065b944197d771eb6486fe7e4627b28059a9b32e852f3a69495fc9b5",
        "format": "zip",
    },
    ("windows", "arm64"): {
        "filename": "ffmpeg-n9.0.1-29-gad500d59cb-winarm64-lgpl-9.0.zip",
        "archive_sha256": "c2b72737f40e0f6206f61c885e011753c5b6307e9d63a2db59a3e949fe24b345",
        "format": "zip",
    },
    ("linux", "x86_64"): {
        "filename": "ffmpeg-n9.0.1-29-gad500d59cb-linux64-lgpl-9.0.tar.xz",
        "archive_sha256": "b8f6a666dac99d2ce2010e35f192ab9696bca4c258b13e33fedf1383defff1ea",
        "format": "tar.xz",
    },
    ("linux", "arm64"): {
        "filename": "ffmpeg-n9.0.1-29-gad500d59cb-linuxarm64-lgpl-9.0.tar.xz",
        "archive_sha256": "ee6b813257af01e125c23740d282fdbc9d656874103ce898f708bdc753b95936",
        "format": "tar.xz",
    },
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


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
        headers={"User-Agent": "InfoMancer-native-packaging/0.9"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _require_hash(label: str, data: bytes, expected: str) -> None:
    actual = _sha256(data)
    if actual != expected:
        raise RuntimeError(
            f"{label} SHA-256 mismatch: expected {expected}, received {actual}"
        )


def _extract_ffprobe(archive: bytes, asset: dict, binary_name: str) -> bytes:
    candidates: list[tuple[str, bytes]] = []
    if asset["format"] == "zip":
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            for member in package.namelist():
                path = PurePosixPath(member)
                if path.name == binary_name and "bin" in path.parts:
                    candidates.append((member, package.read(member)))
    elif asset["format"] == "tar.xz":
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as package:
            for member in package.getmembers():
                path = PurePosixPath(member.name)
                if member.isfile() and path.name == binary_name and "bin" in path.parts:
                    extracted = package.extractfile(member)
                    if extracted is not None:
                        candidates.append((member.name, extracted.read()))
    else:
        raise RuntimeError(f"Unsupported FFprobe archive format: {asset['format']}")

    if len(candidates) != 1:
        found = ", ".join(name for name, _ in candidates) or "none"
        raise RuntimeError(
            f"Expected exactly one {binary_name} in pinned FFmpeg archive; found {found}"
        )
    return candidates[0][1]


def _verify_ffprobe(binary_path: Path) -> str:
    try:
        result = subprocess.run(
            [str(binary_path), "-version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not execute staged FFprobe: {exc}") from exc

    report = result.stdout
    lower = report.casefold()
    if VERSION.casefold() not in lower:
        raise RuntimeError(
            f"Staged FFprobe did not report expected version {VERSION}"
        )
    forbidden = [
        flag for flag in ("--enable-gpl", "--enable-nonfree") if flag in lower
    ]
    if forbidden:
        raise RuntimeError(
            "Refusing to package FFprobe because its configuration enables "
            + ", ".join(forbidden)
        )
    if "configuration:" not in lower:
        raise RuntimeError("FFprobe did not report its build configuration")
    if "--enable-version3" not in lower:
        raise RuntimeError(
            "Pinned BtbN LGPL build did not report the expected --enable-version3 license profile"
        )
    return report


def _write_notice(output: Path, asset: dict) -> None:
    (output / "FFPROBE_NOTICE.txt").write_text(
        "InfoMancer native desktop packages include FFprobe from the FFmpeg "
        "project as a separate executable used only for local media inspection.\n\n"
        f"Bundled build: FFmpeg/FFprobe {VERSION}\n"
        "Effective FFmpeg license profile: GNU LGPL v3\n"
        f"Binary builder: BtbN/FFmpeg-Builds @ {BTBN_COMMIT}\n"
        f"Binary release: {RELEASE_BASE}/{asset['filename']}\n"
        f"Exact FFmpeg source commit: {FFMPEG_COMMIT}\n"
        f"Corresponding source: {FFMPEG_SOURCE_URL}\n"
        f"Build scripts source: {BTBN_SOURCE_URL}\n"
        "Upstream project: https://ffmpeg.org/\n\n"
        "The selected BtbN artifact is the LGPL build variant. Its pinned build "
        "profile enables FFmpeg's version-3 licensing option. InfoMancer's "
        "packaging step executes the staged binary and refuses it if FFprobe's "
        "reported configuration contains --enable-gpl or --enable-nonfree, or "
        "does not contain --enable-version3. FFPROBE_LICENSE.txt contains the "
        "GNU LGPL v3 text from the exact FFmpeg source commit. "
        "FFPROBE_BUILDINFO.txt records the binary's own version/configuration "
        "output and archive checksum.\n\n"
        "FFmpeg/FFprobe is third-party software and is not owned by InfoMancer. "
        "InfoMancer and FFmpeg are separate projects.\n",
        encoding="utf-8",
    )


def _write_build_info(output: Path, asset: dict, report: str) -> None:
    (output / "FFPROBE_BUILDINFO.txt").write_text(
        "InfoMancer FFprobe distribution provenance\n"
        "==========================================\n\n"
        f"FFmpeg version: {VERSION}\n"
        "Effective FFmpeg license profile: GNU LGPL v3\n"
        f"FFmpeg source commit: {FFMPEG_COMMIT}\n"
        f"BtbN build-scripts commit: {BTBN_COMMIT}\n"
        f"BtbN release: {BTBN_RELEASE}\n"
        f"Archive: {asset['filename']}\n"
        f"Archive SHA-256: {asset['archive_sha256']}\n"
        f"Corresponding source: {FFMPEG_SOURCE_URL}\n"
        f"Build scripts source: {BTBN_SOURCE_URL}\n"
        f"License source: {LICENSE_URL}\n\n"
        "No InfoMancer modifications are made to the FFmpeg binary after "
        "extraction from the verified archive.\n\n"
        "ffprobe -version\n"
        "----------------\n"
        f"{report.rstrip()}\n",
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


def stage(output: Path) -> Path:
    key = _platform_key()
    asset = ASSETS.get(key)
    if not asset:
        raise RuntimeError(
            f"No approved LGPL FFprobe asset is configured for {key[0]}/{key[1]}. "
            "Do not substitute an unreviewed GPL/nonfree binary."
        )

    output.mkdir(parents=True, exist_ok=True)
    archive = _download(f"{RELEASE_BASE}/{asset['filename']}")
    _require_hash("FFprobe archive", archive, asset["archive_sha256"])

    binary_name = "ffprobe.exe" if key[0] == "windows" else "ffprobe"
    binary = _extract_ffprobe(archive, asset, binary_name)
    binary_path = output / binary_name
    binary_path.write_bytes(binary)
    if key[0] != "windows":
        binary_path.chmod(
            binary_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    license_text = _download(LICENSE_URL)
    actual_license_sha = _git_blob_sha1(license_text)
    if actual_license_sha != LICENSE_GIT_BLOB_SHA1:
        raise RuntimeError(
            "FFprobe LGPL license Git-blob SHA-1 mismatch: expected "
            f"{LICENSE_GIT_BLOB_SHA1}, received {actual_license_sha}"
        )
    (output / "FFPROBE_LICENSE.txt").write_bytes(license_text)

    report = _verify_ffprobe(binary_path)
    _write_notice(output, asset)
    _write_build_info(output, asset, report)
    print(f"Staged verified LGPLv3 FFprobe {VERSION} at {binary_path}")
    return binary_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage the pinned LGPL FFprobe binary used by native InfoMancer packages"
    )
    parser.add_argument("--output", default="build/ffprobe")
    args = parser.parse_args()
    _write_build_identity()
    stage(Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
