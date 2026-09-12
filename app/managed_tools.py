from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys


FFPROBE_MANAGED_VERSION = "6.1.1"


@dataclass(frozen=True)
class ToolStatus:
    name: str
    healthy: bool
    source: str
    path: str
    version: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def application_data_dir() -> Path:
    explicit = os.environ.get("INFOMANCER_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    database = os.environ.get("INFOMANCER_DATABASE", "").strip()
    if database:
        return Path(database).expanduser().resolve().parent
    return (Path.cwd() / "data").resolve()


def _ffprobe_filename() -> str:
    return "ffprobe.exe" if os.name == "nt" else "ffprobe"


def managed_ffprobe_dir() -> Path:
    return application_data_dir() / "tools" / "ffprobe" / FFPROBE_MANAGED_VERSION


def managed_ffprobe_path() -> Path:
    return managed_ffprobe_dir() / _ffprobe_filename()


def bundled_ffprobe_path() -> Path | None:
    bundle_dir = str(getattr(sys, "_MEIPASS", "") or "").strip()
    if not bundle_dir:
        return None
    candidate = Path(bundle_dir) / _ffprobe_filename()
    return candidate if candidate.is_file() else None


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_ffprobe(path: Path | str, timeout: int = 15) -> ToolStatus:
    candidate = Path(path).expanduser()
    source = "managed" if candidate == managed_ffprobe_path() else "custom"
    if not candidate.is_file():
        return ToolStatus("ffprobe", False, source, str(candidate), error="Executable is missing")
    try:
        result = subprocess.run(
            [str(candidate), "-version"], capture_output=True, text=True,
            timeout=timeout, check=False, **_quiet_subprocess_options(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ToolStatus("ffprobe", False, source, str(candidate), error=str(exc)[:300])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        return ToolStatus("ffprobe", False, source, str(candidate), error=detail[:300])
    first_line = (result.stdout or "").splitlines()[0].strip() if result.stdout else ""
    version = ""
    parts = first_line.split()
    if len(parts) >= 3 and parts[0].casefold() == "ffprobe" and parts[1].casefold() == "version":
        version = parts[2]
    return ToolStatus("ffprobe", True, source, str(candidate), version=version)


def resolve_ffprobe_executable() -> str:
    override = os.environ.get("INFOMANCER_FFPROBE", "").strip()
    if override:
        return override
    managed = managed_ffprobe_path()
    if managed.is_file():
        return str(managed)
    bundled = bundled_ffprobe_path()
    if bundled is not None:
        return str(bundled)
    return shutil.which("ffprobe") or "ffprobe"


def ffprobe_status() -> ToolStatus:
    override = os.environ.get("INFOMANCER_FFPROBE", "").strip()
    if override:
        status = verify_ffprobe(override)
        return ToolStatus(status.name, status.healthy, "override", status.path, status.version, status.error)

    managed = managed_ffprobe_path()
    if managed.is_file():
        return verify_ffprobe(managed)

    bundled = bundled_ffprobe_path()
    if bundled is not None:
        status = verify_ffprobe(bundled)
        return ToolStatus(status.name, status.healthy, "bundled", status.path, status.version, status.error)

    system = shutil.which("ffprobe")
    if system:
        status = verify_ffprobe(system)
        return ToolStatus(status.name, status.healthy, "path", status.path, status.version, status.error)

    return ToolStatus("ffprobe", False, "missing", "", error="FFprobe could not be found")


def bootstrap_managed_ffprobe_from_bundle() -> ToolStatus:
    """Seed the managed runtime from the build-verified bundled copy.

    This is deliberately offline. Network repair remains a separate, explicit 0.9
    operation. The source/destination hashes must match, the staged executable must
    pass `ffprobe -version`, and installation uses an atomic replace.
    """
    target = managed_ffprobe_path()
    if target.is_file():
        healthy = verify_ffprobe(target)
        if healthy.healthy:
            return healthy

    source = bundled_ffprobe_path()
    if source is None:
        return ffprobe_status()

    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".staging")
    staged.unlink(missing_ok=True)
    try:
        shutil.copy2(source, staged)
        if os.name != "nt":
            staged.chmod(staged.stat().st_mode | 0o111)
        if _sha256_file(source) != _sha256_file(staged):
            raise RuntimeError("Bundled FFprobe changed while creating the managed copy")
        staged_status = verify_ffprobe(staged)
        if not staged_status.healthy:
            raise RuntimeError(staged_status.error or "Staged FFprobe self-check failed")
        os.replace(staged, target)

        bundle_license = source.parent / "FFPROBE_LICENSE.txt"
        if bundle_license.is_file():
            license_target = target.parent / "FFPROBE_LICENSE.txt"
            license_stage = license_target.with_name(license_target.name + ".staging")
            shutil.copy2(bundle_license, license_stage)
            os.replace(license_stage, license_target)

        final_status = verify_ffprobe(target)
        if not final_status.healthy:
            raise RuntimeError(final_status.error or "Installed FFprobe self-check failed")
        return final_status
    except Exception as exc:
        staged.unlink(missing_ok=True)
        return ToolStatus("ffprobe", False, "managed", str(target), error=str(exc)[:300])
