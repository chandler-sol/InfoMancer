from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

DIRECTORIES = (
    "app",
    "deploy",
    "docs",
    "scripts",
    "tests",
)
FILES = (
    ".dockerignore",
    ".env.example",
    ".env.cloudflare.example",
    ".env.sandbox.example",
    ".gitignore",
    "compose.cloudflare.yaml",
    "compose.sandbox.yaml",
    "compose.yaml",
    "Dockerfile",
    "infomancer-lockup.svg",
    "README.md",
    "requirements.txt",
    "SECURITY.md",
)
EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".log",
}


def application_version() -> str:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        raise RuntimeError(
            "InfoMancer's version could not be read from app/main.py. "
            "Set APP_VERSION before creating a release."
        )
    return match.group(1)


def included_files() -> list[Path]:
    paths: list[Path] = []
    for name in FILES:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Required release file is missing: {name}")
        paths.append(path)
    for directory in DIRECTORIES:
        root = ROOT / directory
        if not root.is_dir():
            raise RuntimeError(f"Required release folder is missing: {directory}")
        for path in root.rglob("*"):
            relative = path.relative_to(ROOT)
            if (
                path.is_file()
                and not EXCLUDED_PARTS.intersection(relative.parts)
                and path.suffix.casefold() not in EXCLUDED_SUFFIXES
            ):
                paths.append(path)
    return sorted(set(paths), key=lambda item: item.as_posix().casefold())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(version: str) -> Path:
    DIST.mkdir(exist_ok=True)
    package_name = f"InfoMancer-{version}"
    archive = DIST / f"{package_name}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as bundle:
        for path in included_files():
            relative = path.relative_to(ROOT)
            bundle.write(path, Path(package_name) / relative)
    checksum = sha256(archive)
    (DIST / "SHA256SUMS.txt").write_text(
        f"{checksum}  {archive.name}\n", encoding="utf-8"
    )
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a credential-free InfoMancer alpha release ZIP."
    )
    parser.add_argument(
        "--version",
        default=application_version(),
        help="Release version; defaults to APP_VERSION from app/main.py.",
    )
    arguments = parser.parse_args()
    archive = build(arguments.version.strip())
    print(f"Created {archive}")
    print(f"Checksum: {DIST / 'SHA256SUMS.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
