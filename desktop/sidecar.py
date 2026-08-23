from __future__ import annotations

import argparse
import os
import shutil
import string
import sys
from pathlib import Path

DESKTOP_VERSION = "0.8.0-alpha.1"


def _root_is_accessible(root: Path) -> bool:
    """Return whether a browse root can actually be opened by this process."""
    try:
        with os.scandir(root):
            return True
    except OSError:
        return False


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
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:/")
            if _root_is_accessible(drive):
                roots.append(drive)
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


def _ensure_runtime_streams(data_dir: Path) -> None:
    """Give windowed Windows builds a diagnostic stream without opening a console."""
    if os.name != "nt" or (sys.stdout is not None and sys.stderr is not None):
        return
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = (log_dir / "desktop-core.log").open(
        "a", encoding="utf-8", buffering=1
    )
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


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


def main() -> None:
    parser = argparse.ArgumentParser(description="InfoMancer Desktop local core")
    parser.add_argument("--port", type=int)
    # Retained for backwards-compatible manual launches. The native launcher sends
    # this secret through the inherited environment so it is not exposed in process
    # command-line listings.
    parser.add_argument("--bootstrap-token", default="")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--recovery-output")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    _ensure_runtime_streams(data_dir)
    if args.recovery_output:
        try:
            create_recovery_package(data_dir, Path(args.recovery_output))
        except Exception as exc:
            print(f"Recovery package failed: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(2) from exc
        return

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


if __name__ == "__main__":
    main()
