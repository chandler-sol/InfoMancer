from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse


FFMPEG_VERSION = "6.1.1"
FFMPEG_RELEASE_BASE = (
    "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
)
FFMPEG_ASSETS = {
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

_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024
_MAX_LICENSE_BYTES = 2 * 1024 * 1024
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
}
_COMPONENT_LOCK = threading.Lock()


class ManagedFfmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManagedFfmpegStatus:
    state: str
    available: bool
    version: str
    path: str
    detail: str
    can_install: bool
    can_remove: bool


def ffmpeg_platform_key() -> tuple[str, str]:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "windows":
        os_name = "windows"
    elif system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    else:
        raise ManagedFfmpegError(
            f"Managed FFmpeg is not available for operating system: {system or 'unknown'}."
        )

    if machine in {"amd64", "x86_64"}:
        arch = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise ManagedFfmpegError(
            f"Managed FFmpeg is not available for architecture: {machine or 'unknown'}."
        )
    return os_name, arch


def ffmpeg_binary_name(key: tuple[str, str] | None = None) -> str:
    current = key or ffmpeg_platform_key()
    return "ffmpeg.exe" if current[0] == "windows" else "ffmpeg"


def default_data_directory() -> Path:
    raw = Path(os.environ.get("INFOMANCER_DATABASE", "data/infomancer.db"))
    if not raw.is_absolute():
        raw = Path(__file__).resolve().parent.parent / raw
    return raw.parent


def managed_ffmpeg_root(data_directory: Path | None = None) -> Path:
    base = Path(data_directory) if data_directory is not None else default_data_directory()
    return base / "components" / "ffmpeg"


def managed_ffmpeg_directory(data_directory: Path | None = None) -> Path:
    return managed_ffmpeg_root(data_directory) / FFMPEG_VERSION


def managed_ffmpeg_binary(data_directory: Path | None = None) -> Path:
    return managed_ffmpeg_directory(data_directory) / ffmpeg_binary_name()


def managed_ffmpeg_candidate(data_directory: Path | None = None) -> Path | None:
    try:
        candidate = managed_ffmpeg_binary(data_directory)
    except ManagedFfmpegError:
        return None
    if not candidate.is_file():
        return None
    if os.name != "nt" and not os.access(candidate, os.X_OK):
        return None
    return candidate


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_hash(label: str, data: bytes, expected: str) -> None:
    actual = _sha256(data)
    if actual != expected:
        raise ManagedFfmpegError(
            f"{label} failed integrity verification. Expected {expected}, received {actual}."
        )


def _download(url: str, maximum_bytes: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ManagedFfmpegError("Managed FFmpeg downloads must start from the pinned GitHub release.")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "InfoMancer-managed-components/0.9"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final = urlparse(response.geturl())
            if final.scheme != "https" or final.hostname not in _ALLOWED_DOWNLOAD_HOSTS:
                raise ManagedFfmpegError(
                    "The FFmpeg download redirected outside InfoMancer's approved release hosts."
                )
            payload = response.read(maximum_bytes + 1)
    except ManagedFfmpegError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ManagedFfmpegError(
            "InfoMancer could not download the managed FFmpeg component."
        ) from exc
    if len(payload) > maximum_bytes:
        raise ManagedFfmpegError("The managed FFmpeg download exceeded its size limit.")
    return payload


def _decompress_archive(archive: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=BytesIO(archive), mode="rb") as stream:
            binary = stream.read(_MAX_BINARY_BYTES + 1)
    except (OSError, EOFError) as exc:
        raise ManagedFfmpegError("The FFmpeg archive could not be decompressed safely.") from exc
    if len(binary) > _MAX_BINARY_BYTES:
        raise ManagedFfmpegError("The FFmpeg binary exceeded its decompression size limit.")
    return binary


def _quiet_subprocess_options() -> dict[str, object]:
    if os.name != "nt":
        return {}
    options: dict[str, object] = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        options["creationflags"] = create_no_window
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    startf_use_showwindow = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    sw_hide = getattr(subprocess, "SW_HIDE", 0)
    if startupinfo_type is not None and startf_use_showwindow:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= startf_use_showwindow
        startupinfo.wShowWindow = sw_hide
        options["startupinfo"] = startupinfo
    return options


def _verify_executable(path: Path) -> None:
    try:
        result = subprocess.run(
            [str(path), "-version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
            **_quiet_subprocess_options(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedFfmpegError(
            "The downloaded FFmpeg binary could not be started."
        ) from exc
    if result.returncode != 0:
        raise ManagedFfmpegError(
            "The downloaded FFmpeg binary did not pass its startup verification."
        )
    first_line = (result.stdout or b"").decode("utf-8", errors="replace").splitlines()
    if not first_line or "ffmpeg version" not in first_line[0].casefold():
        raise ManagedFfmpegError(
            "The downloaded executable did not identify itself as FFmpeg."
        )


def _bundled_candidate() -> Path | None:
    bundle_dir = getattr(sys, "_MEIPASS", "")
    if not bundle_dir:
        return None
    try:
        candidate = Path(bundle_dir) / ffmpeg_binary_name()
    except ManagedFfmpegError:
        return None
    return candidate if candidate.is_file() else None


def _override_candidate() -> str:
    return os.environ.get("INFOMANCER_FFMPEG", "").strip()


class ManagedFfmpegComponent:
    def __init__(self, data_directory: Path) -> None:
        self.data_directory = Path(data_directory)

    def status(self) -> ManagedFfmpegStatus:
        override = _override_candidate()
        if override:
            resolved = shutil.which(override)
            candidate = Path(override)
            available = bool(resolved or candidate.is_file())
            return ManagedFfmpegStatus(
                state="override",
                available=available,
                version="Custom",
                path=resolved or override,
                detail=(
                    "Using the FFmpeg path explicitly configured by INFOMANCER_FFMPEG."
                    if available
                    else "INFOMANCER_FFMPEG is configured, but that executable is unavailable."
                ),
                can_install=False,
                can_remove=False,
            )

        bundled = _bundled_candidate()
        if bundled is not None:
            return ManagedFfmpegStatus(
                state="bundled",
                available=True,
                version=FFMPEG_VERSION,
                path=str(bundled),
                detail="Using the FFmpeg binary bundled with this InfoMancer package.",
                can_install=False,
                can_remove=False,
            )

        managed = managed_ffmpeg_candidate(self.data_directory)
        if managed is not None:
            return ManagedFfmpegStatus(
                state="managed",
                available=True,
                version=FFMPEG_VERSION,
                path=str(managed),
                detail="Using the FFmpeg copy installed and managed by InfoMancer.",
                can_install=False,
                can_remove=True,
            )

        system = shutil.which("ffmpeg")
        if system:
            return ManagedFfmpegStatus(
                state="system",
                available=True,
                version="System",
                path=system,
                detail="Using FFmpeg already available on this system.",
                can_install=False,
                can_remove=False,
            )

        try:
            ffmpeg_platform_key()
        except ManagedFfmpegError as exc:
            return ManagedFfmpegStatus(
                state="unsupported",
                available=False,
                version="",
                path="",
                detail=str(exc),
                can_install=False,
                can_remove=False,
            )

        return ManagedFfmpegStatus(
            state="unavailable",
            available=False,
            version=FFMPEG_VERSION,
            path="",
            detail="FFmpeg is not available. InfoMancer can install its pinned private copy.",
            can_install=True,
            can_remove=False,
        )

    def install(self) -> Path:
        with _COMPONENT_LOCK:
            return self._install_locked()

    def _install_locked(self) -> Path:
        existing = managed_ffmpeg_candidate(self.data_directory)
        if existing is not None:
            return existing
        if _override_candidate() or _bundled_candidate() is not None:
            raise ManagedFfmpegError(
                "A higher-priority FFmpeg configuration is already active."
            )
        if shutil.which("ffmpeg"):
            raise ManagedFfmpegError(
                "System FFmpeg is already available, so a managed copy is not needed."
            )

        key = ffmpeg_platform_key()
        asset = FFMPEG_ASSETS.get(key)
        if not asset:
            raise ManagedFfmpegError(
                f"No managed FFmpeg build is available for {key[0]}/{key[1]}."
            )

        archive = _download(
            f"{FFMPEG_RELEASE_BASE}/ffmpeg-{asset['slug']}.gz",
            _MAX_ARCHIVE_BYTES,
        )
        _verify_hash("FFmpeg archive", archive, asset["archive_sha256"])
        binary = _decompress_archive(archive)
        _verify_hash("FFmpeg binary", binary, asset["binary_sha256"])
        license_bytes = _download(
            f"{FFMPEG_RELEASE_BASE}/{asset['slug']}.LICENSE",
            _MAX_LICENSE_BYTES,
        )
        _verify_hash("FFmpeg license", license_bytes, asset["license_sha256"])

        root = managed_ffmpeg_root(self.data_directory)
        root.mkdir(parents=True, exist_ok=True)
        staging: Path | None = Path(
            tempfile.mkdtemp(prefix=".install-", dir=root)
        )
        final = managed_ffmpeg_directory(self.data_directory)
        binary_path = staging / ffmpeg_binary_name(key)
        try:
            binary_path.write_bytes(binary)
            if key[0] != "windows":
                binary_path.chmod(
                    binary_path.stat().st_mode
                    | stat.S_IXUSR
                    | stat.S_IXGRP
                    | stat.S_IXOTH
                )
            (staging / "FFMPEG_LICENSE.txt").write_bytes(license_bytes)
            (staging / "FFMPEG_NOTICE.txt").write_text(
                "InfoMancer installed this private FFmpeg copy for bounded local "
                "Episode Identity frame extraction.\n\n"
                f"Version: {FFMPEG_VERSION}\n"
                f"Source: {FFMPEG_RELEASE_BASE}\n"
                "Upstream: https://ffmpeg.org/\n",
                encoding="utf-8",
            )
            (staging / "component.json").write_text(
                json.dumps(
                    {
                        "component": "ffmpeg",
                        "version": FFMPEG_VERSION,
                        "platform": key[0],
                        "architecture": key[1],
                        "binary_sha256": asset["binary_sha256"],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            _verify_executable(binary_path)

            if final.exists():
                if final.is_symlink() or not final.is_dir():
                    raise ManagedFfmpegError(
                        "The managed FFmpeg destination is not a normal directory."
                    )
                shutil.rmtree(final)
            os.replace(staging, final)
            staging = None
        except ManagedFfmpegError:
            raise
        except OSError as exc:
            raise ManagedFfmpegError(
                "InfoMancer could not save the managed FFmpeg component."
            ) from exc
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

        installed = managed_ffmpeg_binary(self.data_directory)
        if not installed.is_file():
            raise ManagedFfmpegError(
                "FFmpeg installation completed without a usable managed binary."
            )
        return installed

    def remove(self) -> None:
        with _COMPONENT_LOCK:
            self._remove_locked()

    def _remove_locked(self) -> None:
        directory = managed_ffmpeg_directory(self.data_directory)
        if not directory.exists():
            return
        if directory.is_symlink() or not directory.is_dir():
            raise ManagedFfmpegError(
                "The managed FFmpeg component path is not a normal directory."
            )
        try:
            shutil.rmtree(directory)
            root = managed_ffmpeg_root(self.data_directory)
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError as exc:
            raise ManagedFfmpegError(
                "InfoMancer could not remove the managed FFmpeg component."
            ) from exc
