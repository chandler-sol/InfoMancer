from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


VERSION = "n9.0.1-29-gad500d59cb"
FFMPEG_COMMIT = "ad500d59cb6e0126add4fcb95afb4e2557c4292c"
SOURCE_ARCHIVE = f"ffmpeg-source-{FFMPEG_COMMIT}.tar.gz"
LICENSE_GIT_BLOB_SHA1 = "40924c2a6da76a2b0c639f6fe7ef0b2d095a6adb"

REQUIRED_CONFIGURE_FLAGS = {
    "--target-os=mingw32",
    "--arch=x86_64",
    "--disable-autodetect",
    "--disable-debug",
    "--disable-doc",
    "--disable-ffmpeg",
    "--disable-ffplay",
    "--disable-network",
    "--disable-avdevice",
    "--disable-devices",
    "--disable-filters",
    "--disable-encoders",
    "--disable-muxers",
    "--disable-hwaccels",
    "--disable-iconv",
    "--disable-pthreads",
    "--enable-w32threads",
    "--disable-x86asm",
    "--enable-static",
    "--disable-shared",
}

FORBIDDEN_CONFIGURE_FLAGS = {
    "--enable-gpl",
    "--enable-nonfree",
    "--enable-version3",
}


class FFprobeComplianceError(RuntimeError):
    """Raised when a candidate FFprobe artifact is unsafe to redistribute."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    """Return Git's blob object ID; SHA-1 here is an object identity, not a security check."""
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _read_nonempty_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verify_input_bundle(input_dir: Path) -> dict[str, Path]:
    files = {
        "binary": input_dir / "ffprobe.exe",
        "license": input_dir / "FFPROBE_LICENSE.txt",
        "build_script": input_dir / "FFPROBE_BUILD_SCRIPT.sh",
        "configure_args": input_dir / "FFPROBE_CONFIGURE_ARGS.txt",
        "dll_dependencies": input_dir / "FFPROBE_DLL_DEPENDENCIES.txt",
        "source": input_dir / SOURCE_ARCHIVE,
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise FFprobeComplianceError(
            "Minimal FFprobe artifact is incomplete; missing: " + ", ".join(missing)
        )
    if files["binary"].stat().st_size < 100_000:
        raise FFprobeComplianceError("Candidate ffprobe.exe is unexpectedly small")
    if files["source"].stat().st_size < 1_000_000:
        raise FFprobeComplianceError("Corresponding FFmpeg source archive is unexpectedly small")
    return files


def _verify_license(path: Path) -> None:
    data = path.read_bytes()
    actual = _git_blob_sha1(data)
    if actual != LICENSE_GIT_BLOB_SHA1:
        raise FFprobeComplianceError(
            "LGPL v2.1 license identity mismatch: expected Git blob "
            f"{LICENSE_GIT_BLOB_SHA1}, received {actual}"
        )


def _verify_configure_args(path: Path) -> list[str]:
    args = _read_nonempty_lines(path)
    configured = set(args)
    missing = sorted(REQUIRED_CONFIGURE_FLAGS - configured)
    if missing:
        raise FFprobeComplianceError(
            "Minimal FFprobe build is missing required configure flags: "
            + ", ".join(missing)
        )

    forbidden = sorted(FORBIDDEN_CONFIGURE_FLAGS & configured)
    external = sorted(flag for flag in args if re.match(r"--enable-lib", flag))
    if forbidden or external:
        raise FFprobeComplianceError(
            "Refusing FFprobe build with disallowed license/external-library flags: "
            + ", ".join(forbidden + external)
        )
    return args


def _verify_dll_dependencies(path: Path) -> list[str]:
    dependencies = _read_nonempty_lines(path)
    if not dependencies:
        raise FFprobeComplianceError("FFprobe DLL dependency inventory is empty")

    # The minimal build may depend on Windows system DLLs, but not on MinGW
    # runtime DLLs or third-party media libraries. Keep this intentionally
    # conservative and expand only after review of a reproducible build.
    forbidden_patterns = (
        "libgcc",
        "libstdc++",
        "libwinpthread",
        "libiconv",
        "libz",
        "avcodec",
        "avformat",
        "avutil",
        "swresample",
        "swscale",
    )
    bad = [
        dep for dep in dependencies
        if any(pattern in dep.casefold() for pattern in forbidden_patterns)
    ]
    if bad:
        raise FFprobeComplianceError(
            "Minimal FFprobe unexpectedly depends on non-system DLLs: " + ", ".join(bad)
        )
    return dependencies


def _verify_ffprobe(binary_path: Path) -> str:
    if os.name != "nt":
        raise FFprobeComplianceError(
            "The Windows FFprobe redistribution check must execute on Windows"
        )
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
        raise FFprobeComplianceError(f"Could not execute candidate FFprobe: {exc}") from exc

    report = result.stdout
    lower = report.casefold()
    if VERSION.casefold() not in lower:
        raise FFprobeComplianceError(
            f"Candidate FFprobe did not report expected version {VERSION}"
        )
    if "configuration:" not in lower:
        raise FFprobeComplianceError("Candidate FFprobe did not report its build configuration")

    forbidden = [
        flag
        for flag in ("--enable-gpl", "--enable-nonfree", "--enable-version3")
        if flag in lower
    ]
    external = sorted(set(re.findall(r"--enable-lib[a-z0-9_-]+", lower)))
    if forbidden or external:
        raise FFprobeComplianceError(
            "Refusing FFprobe binary with disallowed license/external-library configuration: "
            + ", ".join(forbidden + external)
        )
    for required in REQUIRED_CONFIGURE_FLAGS:
        if required.casefold() not in lower:
            raise FFprobeComplianceError(
                f"Candidate FFprobe runtime configuration is missing {required}"
            )
    return report


def _write_notice(output: Path) -> None:
    (output / "FFPROBE_NOTICE.txt").write_text(
        "InfoMancer native Windows packages include FFprobe from the FFmpeg "
        "project as a separate executable used only for local media inspection.\n\n"
        f"Bundled FFmpeg source commit: {FFMPEG_COMMIT}\n"
        f"Bundled FFprobe version: {VERSION}\n"
        "Effective FFmpeg license profile: GNU LGPL v2.1 or later\n"
        "Upstream project: https://ffmpeg.org/\n\n"
        "InfoMancer builds this FFprobe executable from the exact FFmpeg source "
        "commit with --disable-autodetect and without optional external media "
        "libraries, GPL components, nonfree components, or the version-3-only "
        "license profile. The build also disables FFmpeg, FFplay, networking, "
        "encoders, muxers, filters, devices, and hardware acceleration because "
        "InfoMancer only needs local metadata inspection.\n\n"
        "FFPROBE_LICENSE.txt contains FFmpeg's GNU LGPL v2.1 license text. "
        "FFPROBE_BUILDINFO.txt records the binary hash, source hash, exact "
        "configuration, DLL dependency inventory, and the binary's own "
        "ffprobe -version output. FFPROBE_BUILD_SCRIPT.sh is the exact build "
        "recipe. The corresponding FFmpeg source archive is published beside "
        "each native release that contains this binary.\n\n"
        "FFmpeg/FFprobe is third-party software and is not owned by InfoMancer. "
        "InfoMancer and FFmpeg are separate projects.\n",
        encoding="utf-8",
    )


def _write_build_info(
    output: Path,
    files: dict[str, Path],
    configure_args: list[str],
    dependencies: list[str],
    report: str,
) -> None:
    (output / "FFPROBE_BUILDINFO.txt").write_text(
        "InfoMancer FFprobe distribution provenance\n"
        "==========================================\n\n"
        f"FFmpeg version: {VERSION}\n"
        f"FFmpeg source commit: {FFMPEG_COMMIT}\n"
        "Effective FFmpeg license profile: GNU LGPL v2.1 or later\n"
        f"ffprobe.exe SHA-256: {_sha256_file(files['binary'])}\n"
        f"Corresponding source archive: {SOURCE_ARCHIVE}\n"
        f"Corresponding source SHA-256: {_sha256_file(files['source'])}\n\n"
        "Configure arguments\n"
        "-------------------\n"
        + "\n".join(configure_args)
        + "\n\nDLL dependencies\n"
        "----------------\n"
        + "\n".join(dependencies)
        + "\n\nffprobe -version\n"
        "----------------\n"
        + report.rstrip()
        + "\n",
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
        json.dumps({"commit": commit, "short_commit": short_commit}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Stamped InfoMancer runtime build identity: {short_commit}")
    return path


def stage(input_dir: Path, output: Path) -> Path:
    files = _verify_input_bundle(input_dir)
    _verify_license(files["license"])
    configure_args = _verify_configure_args(files["configure_args"])
    dependencies = _verify_dll_dependencies(files["dll_dependencies"])
    report = _verify_ffprobe(files["binary"])

    output.mkdir(parents=True, exist_ok=True)
    binary_path = output / "ffprobe.exe"
    shutil.copy2(files["binary"], binary_path)
    shutil.copy2(files["license"], output / "FFPROBE_LICENSE.txt")
    shutil.copy2(files["build_script"], output / "FFPROBE_BUILD_SCRIPT.sh")
    shutil.copy2(files["configure_args"], output / "FFPROBE_CONFIGURE_ARGS.txt")
    shutil.copy2(files["dll_dependencies"], output / "FFPROBE_DLL_DEPENDENCIES.txt")
    _write_notice(output)
    _write_build_info(output, files, configure_args, dependencies, report)
    print(f"Staged minimal LGPL FFprobe {VERSION} at {binary_path}")
    return binary_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and stage InfoMancer's minimal FFprobe Windows build"
    )
    parser.add_argument("--input", default="build/minimal-ffprobe")
    parser.add_argument("--output", default="build/ffprobe")
    args = parser.parse_args()
    _write_build_identity()
    stage(Path(args.input), Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
