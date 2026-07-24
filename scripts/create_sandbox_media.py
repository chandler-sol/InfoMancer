from __future__ import annotations

import argparse
from pathlib import Path


FILES = (
    "movies/0-9/12 Angry Men (1957).mkv",
    "movies/0-9/2001 A Space Odyssey (1968).mp4",
    "movies/A/Arrival (2016).mkv",
    "movies/B/Blade Runner (1982).mkv",
    "movies/N/Night of the Living Dead (1968)/Night of the Living Dead (1968).mkv",
    "movies/T/The General (1926).mkv",
    "tv/Example Adventures (2021 - Present)/Season 01/Example Adventures - S01E01 - The Beginning.mkv",
    "tv/Example Adventures (2021 - Present)/Season 01/Example Adventures - S01E02-E03 - The Journey + The Return.mkv",
    "tv/Example Adventures (2021 - Present)/Season 02/Example.Adventures.S02E01.1080p.x265.mkv",
    "tv/City Hospital (2018 - 2020)/Season 01/City Hospital - S01E01 - First Shift.mkv",
    "tv/City Hospital (2018 - 2020)/Season 01/City Hospital - S01E03 - Night Call.mkv",
    "tv/Unmatched Mystery Show (2024)/Season 01/Unmatched Mystery Show S01E01.mkv",
)


def build(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative in FILES:
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    (root / "movies" / "README.txt").write_text(
        "Generated fixture media for InfoMancer's isolated sandbox.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="sandbox-media")
    arguments = parser.parse_args()
    build(Path(arguments.root).resolve())
    print(f"Created sandbox media in {Path(arguments.root).resolve()}")
