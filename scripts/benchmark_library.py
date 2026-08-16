#!/usr/bin/env python3
"""Build disposable synthetic TV libraries and benchmark core 0.8 catalog work.

This intentionally creates real tiny .mkv files so Scanner and fingerprinting exercise
filesystem traversal/stat/open behavior. The files contain no media payload, so this does not
benchmark FFprobe decode/inspection throughput. Run FFprobe/media-inspection qualification against
a representative real-media corpus separately and record it beside these results.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Database
from app.file_hashes import MediaHashService
from app.maintenance import create_database_backup
from app.recovery_package import RecoveryPackageService
from app.scanner import scan_root


def timed(function):
    started = time.perf_counter()
    value = function()
    return time.perf_counter() - started, value


def create_files(root: Path, count: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        show_number = index // 100
        within_show = index % 100
        season = within_show // 25 + 1
        episode = within_show % 25 + 1
        folder = root / f"Synthetic Show {show_number:05d} (2020)" / f"Season {season:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (
            f"Synthetic Show {show_number:05d} - S{season:02d}E{episode:02d} - "
            f"Episode {episode:02d}.mkv"
        )
        path.touch()


def scan(database: Database, root_id: int) -> dict:
    with database.connect() as conn:
        root_row = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
        return scan_root(conn, root_row)


def representative_queries(database: Database) -> dict[str, float | int]:
    results: dict[str, float | int] = {}

    def measure(name: str, sql: str, params=()):
        def run():
            with database.connect() as conn:
                return conn.execute(sql, params).fetchall()
        duration, rows = timed(run)
        results[name] = duration
        results[f"{name}_rows"] = len(rows)

    measure(
        "library_page_seconds",
        """SELECT t.id,COALESCE(t.metadata_title,t.title) display_title,t.year,
                  COUNT(f.id) file_count,COALESCE(SUM(f.size_bytes),0) total_size
           FROM titles t LEFT JOIN files f ON f.title_id=t.id
           GROUP BY t.id ORDER BY display_title COLLATE NOCASE LIMIT 100""",
    )
    measure(
        "search_seconds",
        """SELECT DISTINCT t.id,COALESCE(t.metadata_title,t.title) display_title
           FROM titles t LEFT JOIN files f ON f.title_id=t.id
           WHERE COALESCE(t.metadata_title,t.title) LIKE ? OR f.filename LIKE ?
           ORDER BY display_title COLLATE NOCASE LIMIT 100""",
        ("%Synthetic Show 00010%", "%S02E10%"),
    )
    with database.connect() as conn:
        title = conn.execute("SELECT id FROM titles ORDER BY id LIMIT 1").fetchone()
        title_id = int(title["id"]) if title else 0
    if title_id:
        measure(
            "inspector_season_aggregate_seconds",
            """SELECT season,COUNT(*) file_count,COALESCE(SUM(size_bytes),0) total_size
               FROM files WHERE title_id=? GROUP BY season ORDER BY season""",
            (title_id,),
        )
    measure(
        "review_query_seconds",
        """SELECT category,severity,COUNT(*) count
           FROM mie_findings WHERE status='active'
           GROUP BY category,severity ORDER BY category,severity""",
    )
    return results


def benchmark_once(count: int, hash_limit: int, keep: Path | None = None) -> dict:
    temporary = None
    if keep is None:
        temporary = tempfile.TemporaryDirectory(prefix=f"infomancer-bench-{count}-")
        base = Path(temporary.name)
    else:
        base = keep / str(count)
        base.mkdir(parents=True, exist_ok=True)
    media = base / "media"
    db_path = base / "infomancer.db"

    create_seconds, _ = timed(lambda: create_files(media, count))
    database = Database(db_path)
    database.initialize()
    with database.connect() as conn:
        root_id = conn.execute(
            "INSERT INTO roots(path,kind,label,health_status) VALUES (?,?,?,?)",
            (str(media), "tv", f"Synthetic {count}", "healthy"),
        ).lastrowid

    initial_scan_seconds, scan_result = timed(lambda: scan(database, root_id))
    incremental_scan_seconds, incremental_result = timed(lambda: scan(database, root_id))
    query_results = representative_queries(database)

    hashes = MediaHashService(database)
    eligible_seconds, eligible = timed(hashes.eligible_ids)
    sample = eligible[: max(0, min(hash_limit, len(eligible)))]
    queue_seconds, queued = timed(lambda: hashes.queue(sample))
    hash_seconds = 0.0
    if queued:
        hash_seconds, _ = timed(lambda: hashes.hash_many(queued, intensity="full"))

    backup_seconds, backup = timed(lambda: create_database_backup(db_path, "benchmark"))
    recovery = RecoveryPackageService(db_path, "0.8-benchmark")
    recovery_seconds, package = timed(recovery.create)

    with database.connect() as conn:
        title_count = int(conn.execute("SELECT COUNT(*) count FROM titles").fetchone()["count"])
        file_count = int(conn.execute("SELECT COUNT(*) count FROM files").fetchone()["count"])

    result = {
        "requested_files": count,
        "catalog_titles": title_count,
        "catalog_files": file_count,
        "create_fixture_seconds": create_seconds,
        "initial_scan_seconds": initial_scan_seconds,
        "incremental_scan_seconds": incremental_scan_seconds,
        "scan_result": scan_result,
        "incremental_result": incremental_result,
        **query_results,
        "fingerprint_eligible_query_seconds": eligible_seconds,
        "fingerprint_eligible_count": len(eligible),
        "fingerprint_queue_seconds": queue_seconds,
        "fingerprint_hashed_files": len(queued),
        "fingerprint_hash_seconds": hash_seconds,
        "database_backup_seconds": backup_seconds,
        "database_backup_bytes": backup.stat().st_size,
        "portable_recovery_seconds": recovery_seconds,
        "portable_recovery_bytes": package.stat().st_size,
        "fixture_path": str(base) if keep else "temporary",
        "note": (
            "Synthetic files are zero-byte containers. Record FFprobe/media-inspection and HTTP "
            "rendering measurements separately with representative media and the deployed app."
        ),
    }
    if temporary:
        temporary.cleanup()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, action="append", dest="counts",
        help="File count to benchmark. Repeat for multiple scales. Defaults to 1k/10k/50k/100k.",
    )
    parser.add_argument("--runs", type=int, default=1, help="Runs per scale. Use 3 for release qualification.")
    parser.add_argument(
        "--hash-limit", type=int, default=1000,
        help="Maximum real files to SHA-256 per run. Use a large value for an all-file fingerprint pass.",
    )
    parser.add_argument(
        "--keep", type=Path,
        help="Keep generated fixtures/results beneath this directory instead of using temp storage.",
    )
    parser.add_argument("--output", type=Path, help="Write JSON results to this path.")
    args = parser.parse_args()
    counts = args.counts or [1_000, 10_000, 50_000, 100_000]
    if any(count <= 0 for count in counts) or args.runs <= 0 or args.hash_limit < 0:
        parser.error("counts/runs must be positive and hash-limit cannot be negative")

    all_results = []
    for count in counts:
        runs = []
        for run_number in range(1, args.runs + 1):
            print(f"Benchmarking {count:,} files, run {run_number}/{args.runs}...", flush=True)
            result = benchmark_once(count, args.hash_limit, args.keep)
            result["run"] = run_number
            runs.append(result)
            all_results.append(result)
        if len(runs) > 1:
            for key in (
                "initial_scan_seconds", "incremental_scan_seconds", "search_seconds",
                "library_page_seconds", "inspector_season_aggregate_seconds",
                "fingerprint_hash_seconds", "database_backup_seconds", "portable_recovery_seconds",
            ):
                values = [float(item[key]) for item in runs if key in item]
                if values:
                    print(f"  median {key}: {statistics.median(values):.4f}s")

    payload = {"results": all_results}
    rendered = json.dumps(payload, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
