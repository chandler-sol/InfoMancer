from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from .config import Settings, get_settings
from .db import Database
from .event_log import EventLog
from .media_info import MediaInspectionError, inspect_media
from .scanner import scan_root


EXPORT_FIELDS = [
    "title_id", "kind", "title", "release_year", "end_year", "continuing",
    "tvdb_id", "tvdb_movie_id", "tmdb_id", "imdb_id", "imdb_rating",
    "imdb_votes", "imdb_title_type", "genres", "date_added", "source",
    "source_path", "file_id", "file_path", "filename", "size_bytes",
    "season", "episode_start", "episode_end", "runtime_seconds", "width",
    "height", "video_codec", "audio_codec", "audio_channels", "bitrate",
    "container", "dynamic_range", "media_info_at", "media_info_error",
    "tags", "collections", "custom_fields",
]


class CliError(RuntimeError):
    """An expected command failure with a message suitable for an end user."""


def _database(settings: Settings) -> Database:
    database = Database(settings.database)
    if not database.path.exists():
        raise CliError(
            f"InfoMancer's database was not found at {database.path}. "
            "Start InfoMancer once to create the application data, or check "
            "INFOMANCER_DATABASE if this is an existing installation."
        )
    database.initialize()
    return database


def _human_size(value: int | None) -> str:
    size = float(value or 0)
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or suffix == "TB":
            return f"{size:.0f} {suffix}" if suffix == "B" else f"{size:.1f} {suffix}"
        size /= 1024
    return f"{size:.1f} TB"


def _confirm(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise CliError(f"{prompt} Re-run this command with --yes to confirm.")
    answer = input(f"{prompt} [y/N] ").strip().casefold()
    if answer not in {"y", "yes"}:
        raise CliError("Cancelled. InfoMancer did not make any changes.")


def command_status(database: Database, _args: argparse.Namespace) -> int:
    with database.connect() as conn:
        counts = conn.execute(
            """SELECT
               (SELECT COUNT(*) FROM roots WHERE enabled=1) roots,
               (SELECT COUNT(*) FROM titles WHERE kind='movie') movies,
               (SELECT COUNT(*) FROM titles WHERE kind='tv') shows,
               (SELECT COUNT(*) FROM files) files,
               (SELECT COUNT(*) FROM files WHERE media_info_at IS NOT NULL) inspected,
               (SELECT COUNT(*) FROM titles
                  WHERE tvdb_id IS NULL AND tvdb_movie_id IS NULL
                    AND tmdb_id IS NULL AND imdb_id IS NULL) unmatched"""
        ).fetchone()
        roots = conn.execute(
            """SELECT r.id,r.label,r.path,r.kind,r.enabled,r.last_scanned_at,
                      COUNT(DISTINCT t.id) titles,COUNT(f.id) files
               FROM roots r LEFT JOIN titles t ON t.root_id=r.id
               LEFT JOIN files f ON f.title_id=t.id
               GROUP BY r.id ORDER BY r.kind,r.label,r.path"""
        ).fetchall()
    print("InfoMancer status")
    print(f"Database: {database.path}")
    print(
        f"Sources: {counts['roots']}  Movies: {counts['movies']:,}  "
        f"TV shows: {counts['shows']:,}  Media files: {counts['files']:,}"
    )
    print(
        f"Inspected files: {counts['inspected']:,}  "
        f"Unmatched titles: {counts['unmatched']:,}"
    )
    if roots:
        print("\nSources")
        for root in roots:
            state = "enabled" if root["enabled"] else "disabled"
            label = root["label"] or Path(root["path"]).name or root["path"]
            scanned = root["last_scanned_at"] or "never"
            print(
                f"  {root['id']:>3}  {label} ({root['kind']}, {state})"
                f" — {root['titles']:,} titles, {root['files']:,} files"
            )
            print(f"       {root['path']} — last scan: {scanned}")
    else:
        print("\nNo media sources have been configured yet.")
    return 0


def command_doctor(
    database: Database, settings: Settings, _args: argparse.Namespace
) -> int:
    problems = 0
    print("InfoMancer diagnostics")
    print(f"[OK] Application data directory: {database.path.parent}")
    try:
        with database.connect() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            roots = conn.execute(
                "SELECT id,label,path,enabled FROM roots ORDER BY id"
            ).fetchall()
        if integrity == "ok":
            print("[OK] Database integrity check passed.")
        else:
            problems += 1
            print(f"[ERROR] SQLite reported a database problem: {integrity}")
    except sqlite3.Error as exc:
        raise CliError(
            "InfoMancer could not read its database. "
            f"The database was not changed. Technical detail: {exc}"
        ) from exc

    if shutil.which("ffprobe"):
        print("[OK] FFprobe is available for media inspection.")
    else:
        problems += 1
        print(
            "[WARNING] FFprobe was not found. Catalog scans will still work, "
            "but runtime, resolution, codec, and HDR inspection will not."
        )

    for root in roots:
        if not root["enabled"]:
            continue
        path = Path(root["path"])
        label = root["label"] or path.name or str(path)
        if path.exists() and path.is_dir():
            print(f"[OK] Source is accessible: {label} ({path})")
        else:
            problems += 1
            print(
                f"[ERROR] Source is not accessible: {label} ({path}). "
                "Mount or reconnect the storage, then run this check again."
            )

    if settings.tvdb_api_key:
        print("[OK] A TheTVDB API key is configured.")
    else:
        problems += 1
        print(
            "[WARNING] TheTVDB is not configured. Scanning works, but matching "
            "and missing-episode data will be unavailable."
        )
    print(
        f"\nDiagnostics finished with {problems} "
        f"issue{'s' if problems != 1 else ''} requiring attention."
    )
    return 1 if problems else 0


def _selected_roots(
    database: Database, source: str | None, all_sources: bool
) -> list[sqlite3.Row]:
    with database.connect() as conn:
        if all_sources:
            return conn.execute(
                "SELECT * FROM roots WHERE enabled=1 ORDER BY id"
            ).fetchall()
        if not source:
            raise CliError("Choose a source with --source, or use --all.")
        if source.isdigit():
            rows = conn.execute(
                "SELECT * FROM roots WHERE id=? AND enabled=1", (int(source),)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM roots WHERE enabled=1
                   AND (label=? COLLATE NOCASE OR path=? COLLATE NOCASE)""",
                (source, source),
            ).fetchall()
    if not rows:
        raise CliError(
            f'No enabled source matched "{source}". Run "infomancer status" '
            "to see available source IDs and names."
        )
    if len(rows) > 1:
        raise CliError(
            f'More than one source is named "{source}". Use its numeric source ID instead.'
        )
    return rows


def command_scan(database: Database, args: argparse.Namespace) -> int:
    roots = _selected_roots(database, args.source, args.all)
    if not roots:
        raise CliError("There are no enabled sources to scan.")
    _confirm(
        f"Scan {len(roots)} source{'s' if len(roots) != 1 else ''}? "
        "Files no longer present will be removed from the catalog.",
        args.yes,
    )
    log = EventLog(database)
    failures = 0
    for root in roots:
        label = root["label"] or root["path"]
        print(f"Scanning {label}...")
        last_report = 0

        def progress(files: int, titles: int) -> None:
            nonlocal last_report
            if files == 0 or files - last_report >= 500:
                print(f"  {files:,} files across {titles:,} titles found")
                last_report = files

        try:
            with database.connect() as conn:
                result = scan_root(conn, root, progress)
            print(
                f"  Complete: {result['titles']:,} titles and "
                f"{result['files']:,} video files."
            )
            log.write(
                "scan", f"CLI scan completed for {label}.",
                context={
                    "root_id": root["id"], "titles": result["titles"],
                    "files": result["files"],
                },
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            failures += 1
            print(
                f"  ERROR: {label} could not be scanned. Nothing was removed "
                f"from that source during the failed scan. Reason: {exc}",
                file=sys.stderr,
            )
            log.write(
                "scan", f"CLI scan failed for {label}.", level="error",
                detail=str(exc), context={"root_id": root["id"]},
            )
    return 1 if failures else 0


def _inspection_rows(
    database: Database, title: str | None, inspect_all: bool
) -> list[sqlite3.Row]:
    conditions: list[str] = []
    params: list[object] = []
    if not inspect_all:
        conditions.append(
            "(f.media_info_at IS NULL OR "
            "(f.media_info_error IS NOT NULL AND f.media_info_error!=''))"
        )
    if title:
        if title.isdigit():
            conditions.append("t.id=?")
            params.append(int(title))
        else:
            conditions.append(
                "(t.title LIKE ? OR t.metadata_title LIKE ? COLLATE NOCASE)"
            )
            params.extend([title, title])
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with database.connect() as conn:
        return conn.execute(
            f"""SELECT f.id,f.path,f.filename,t.id title_id,
                       COALESCE(t.metadata_title,t.title) title
                FROM files f JOIN titles t ON t.id=f.title_id
                {where} ORDER BY t.title COLLATE NOCASE,f.id""",
            params,
        ).fetchall()


def command_inspect(database: Database, args: argparse.Namespace) -> int:
    rows = _inspection_rows(database, args.title, args.all)
    if not rows:
        print("No media files need inspection for that selection.")
        return 0
    _confirm(
        f"Inspect {len(rows):,} media file{'s' if len(rows) != 1 else ''} with FFprobe?",
        args.yes,
    )
    log = EventLog(database)
    updated = errors = 0
    for index, row in enumerate(rows, start=1):
        print(f"[{index:,}/{len(rows):,}] {row['title']} — {row['filename']}")
        try:
            values = inspect_media(Path(row["path"]))
            with database.connect() as conn:
                conn.execute(
                    """UPDATE files SET runtime_seconds=?,width=?,height=?,
                       video_codec=?,audio_codec=?,audio_channels=?,bitrate=?,
                       container=?,dynamic_range=?,media_info_at=CURRENT_TIMESTAMP,
                       media_info_error=NULL WHERE id=?""",
                    (
                        values["runtime_seconds"], values["width"], values["height"],
                        values["video_codec"], values["audio_codec"],
                        values["audio_channels"], values["bitrate"],
                        values["container"], values["dynamic_range"], row["id"],
                    ),
                )
            updated += 1
        except MediaInspectionError as exc:
            errors += 1
            with database.connect() as conn:
                conn.execute(
                    """UPDATE files SET media_info_at=CURRENT_TIMESTAMP,
                       media_info_error=? WHERE id=?""",
                    (str(exc), row["id"]),
                )
            print(f"  WARNING: {exc.headline}", file=sys.stderr)
            print(f"  {exc.user_message}", file=sys.stderr)
            log.write(
                "media", f"{exc.headline}: {row['filename']}",
                level="warning", detail=exc.log_detail,
                context={"file_id": row["id"], "path": row["path"]},
            )
    log.write(
        "media", "CLI media inspection finished.",
        level="warning" if errors else "info",
        context={"files": len(rows), "updated": updated, "errors": errors},
    )
    print(f"Inspection finished: {updated:,} updated, {errors:,} failed.")
    return 1 if errors else 0


def _export_rows(database: Database, username: str | None) -> list[dict]:
    user_id: int | None = None
    with database.connect() as conn:
        if username:
            user = conn.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)
            ).fetchone()
            if not user:
                raise CliError(
                    f'No user is named "{username}". Omit --user to export shared '
                    "library data without personal favorites and tags."
                )
            user_id = user["id"]
        rows = conn.execute(
            """SELECT t.id title_id,t.kind,COALESCE(t.metadata_title,t.title) title,
               COALESCE(t.metadata_year,t.year) release_year,
               COALESCE(t.metadata_end_year,t.end_year) end_year,
               COALESCE(t.metadata_continuing,t.continuing) continuing,
               t.tvdb_id,t.tvdb_movie_id,t.tmdb_id,t.imdb_id,t.imdb_rating,
               t.imdb_votes,t.imdb_title_type,t.genres,t.discovered_at date_added,
               r.label source,r.path source_path,f.id file_id,f.path file_path,
               f.filename,f.size_bytes,f.season,f.episode_start,f.episode_end,
               f.runtime_seconds,f.width,f.height,f.video_codec,f.audio_codec,
               f.audio_channels,f.bitrate,f.container,f.dynamic_range,
               f.media_info_at,f.media_info_error
               FROM titles t JOIN roots r ON r.id=t.root_id
               LEFT JOIN files f ON f.title_id=t.id
               ORDER BY t.kind,title COLLATE NOCASE,f.season,f.episode_start,
                        f.filename COLLATE NOCASE"""
        ).fetchall()
        state: dict[int, dict] = {}
        tags: dict[int, list[str]] = {}
        collections: dict[int, list[str]] = {}
        for row in conn.execute(
            """SELECT ct.title_id,c.name FROM collection_titles ct
               JOIN collections c ON c.id=ct.collection_id
               ORDER BY c.name COLLATE NOCASE"""
        ):
            collections.setdefault(row["title_id"], []).append(row["name"])
        if user_id is not None:
            state = {
                row["title_id"]: dict(row)
                for row in conn.execute(
                    """SELECT title_id,favorite,personal_rating,custom_order,sort_title
                       FROM user_title_state WHERE user_id=?""", (user_id,)
                )
            }
            for row in conn.execute(
                """SELECT tt.title_id,ut.name FROM title_tags tt
                   JOIN user_tags ut ON ut.id=tt.tag_id
                   WHERE ut.user_id=? ORDER BY ut.name COLLATE NOCASE""",
                (user_id,),
            ):
                tags.setdefault(row["title_id"], []).append(row["name"])
    exported: list[dict] = []
    for row in rows:
        item = dict(row)
        personal = state.get(row["title_id"], {})
        item["tags"] = ", ".join(tags.get(row["title_id"], []))
        item["collections"] = ", ".join(collections.get(row["title_id"], []))
        item["custom_fields"] = json.dumps(
            {
                "favorite": bool(personal.get("favorite", 0)),
                "personal_rating": personal.get("personal_rating"),
                "custom_order": personal.get("custom_order"),
                "sort_title": personal.get("sort_title"),
            },
            ensure_ascii=False,
        )
        exported.append(item)
    return exported


def command_export(database: Database, args: argparse.Namespace) -> int:
    rows = _export_rows(database, args.user)
    destination = Path(args.output).expanduser() if args.output else Path.cwd()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if destination.exists() and destination.is_dir():
        destination /= f"infomancer-library-{stamp}.{args.format}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "csv":
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    elif args.format == "json":
        destination.write_text(
            json.dumps(
                {"exported_at": datetime.now(timezone.utc).isoformat(), "items": rows},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    else:
        root = ElementTree.Element(
            "infomancer-library",
            exported_at=datetime.now(timezone.utc).isoformat(),
        )
        for row in rows:
            item = ElementTree.SubElement(root, "media-file")
            for key, value in row.items():
                field = ElementTree.SubElement(item, key.replace("_", "-"))
                field.text = "" if value is None else str(value)
        ElementTree.ElementTree(root).write(
            destination, encoding="utf-8", xml_declaration=True
        )
    print(f"Exported {len(rows):,} media-file records to {destination.resolve()}")
    EventLog(database).write(
        "export", f"Library exported as {args.format.upper()} from the CLI.",
        context={"rows": len(rows), "path": str(destination.resolve())},
    )
    return 0


def _print_log_rows(rows: list[sqlite3.Row], newest_first: bool = False) -> None:
    ordered = rows if newest_first else list(reversed(rows))
    for row in ordered:
        print(
            f"{row['created_at']}  {row['level'].upper():<7} "
            f"{row['category']:<16} {row['message']}"
        )
        if row["detail"]:
            print(f"    {row['detail']}")


def command_logs(database: Database, args: argparse.Namespace) -> int:
    log = EventLog(database)
    if args.export:
        rows = log.query(
            level=args.level, category=args.category, search=args.search, limit=50000
        )
        destination = Path(args.export).expanduser()
        if destination.exists() and destination.is_dir():
            destination /= (
                f"infomancer-logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "id", "created_at", "level", "category", "message",
                    "detail", "context_json", "user_name",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(dict(row) for row in reversed(rows))
        print(f"Exported {len(rows):,} log entries to {destination.resolve()}")
        return 0

    rows = log.query(
        level=args.level, category=args.category,
        search=args.search, limit=args.limit,
    )
    _print_log_rows(rows)
    if not args.follow:
        return 0
    last_id = max((row["id"] for row in rows), default=0)
    print("Following new log entries. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
            with database.connect() as conn:
                new_rows = conn.execute(
                    """SELECT e.*,COALESCE(u.display_name,'System') user_name
                       FROM event_logs e LEFT JOIN users u ON u.id=e.user_id
                       WHERE e.id>? ORDER BY e.id""", (last_id,)
                ).fetchall()
            if new_rows:
                _print_log_rows(new_rows, newest_first=True)
                last_id = new_rows[-1]["id"]
    except KeyboardInterrupt:
        print("\nStopped following logs.")
    return 0


def command_backup(database: Database, args: argparse.Namespace) -> int:
    destination = Path(args.output).expanduser() if args.output else Path.cwd()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if destination.exists() and destination.is_dir():
        destination /= f"infomancer-backup-{stamp}.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.resolve() == database.path.resolve():
        raise CliError("The backup destination cannot be the live InfoMancer database.")
    try:
        source = sqlite3.connect(database.path)
        target = sqlite3.connect(destination)
        with target:
            source.backup(target)
        source.close()
        target.close()
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise CliError(
            "The backup could not be created. The live catalog was not changed. "
            f"Technical detail: {exc}"
        ) from exc
    print(
        f"Backup created at {destination.resolve()} "
        f"({_human_size(destination.stat().st_size)})"
    )
    EventLog(database).write(
        "backup", "A database backup was created from the CLI.",
        context={"path": str(destination.resolve())},
    )
    return 0


def command_optimize(database: Database, args: argparse.Namespace) -> int:
    _confirm(
        "Optimize the database indexes and query statistics?", args.yes
    )
    try:
        with database.connect() as conn:
            conn.execute("ANALYZE")
            conn.execute("PRAGMA optimize")
        with database.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        raise CliError(
            "Database optimization could not finish. The catalog was not "
            f"deleted. Technical detail: {exc}"
        ) from exc
    print("Database indexes and query statistics optimized successfully.")
    EventLog(database).write(
        "database", "Database optimized successfully from the CLI."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infomancer",
        description="Manage and troubleshoot an InfoMancer installation.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="Show catalog and source status.")
    commands.add_parser(
        "doctor", help="Check the database, storage, FFprobe, and metadata setup."
    )

    scan = commands.add_parser("scan", help="Reconcile media sources with the catalog.")
    scan_group = scan.add_mutually_exclusive_group(required=True)
    scan_group.add_argument("--all", action="store_true", help="Scan every enabled source.")
    scan_group.add_argument(
        "--source", metavar="ID_OR_NAME", help="Scan one source by ID, label, or exact path."
    )
    scan.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")

    inspect = commands.add_parser(
        "inspect", help="Collect runtime, resolution, codec, and HDR information."
    )
    inspect.add_argument(
        "--all", action="store_true",
        help="Reinspect files that already have media information.",
    )
    inspect.add_argument(
        "--title", metavar="ID_OR_EXACT_TITLE",
        help="Limit inspection to one title ID or exact title.",
    )
    inspect.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")

    export = commands.add_parser(
        "export", help="Export the library as CSV, JSON, or XML."
    )
    export.add_argument(
        "--format", choices=("csv", "json", "xml"), default="csv"
    )
    export.add_argument(
        "--output", "-o", help="Destination file or existing directory."
    )
    export.add_argument(
        "--user", help="Include this user's favorites, ratings, order, and tags."
    )

    logs = commands.add_parser("logs", help="Read, follow, or export application logs.")
    logs.add_argument("--level", choices=("debug", "info", "warning", "error"), default="")
    logs.add_argument("--category", default="")
    logs.add_argument("--search", default="")
    logs.add_argument("--limit", type=int, default=100)
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--export", metavar="FILE_OR_DIRECTORY")

    backup = commands.add_parser(
        "backup", help="Create a consistent backup of the live SQLite catalog."
    )
    backup.add_argument("--output", "-o", help="Destination file or existing directory.")

    optimize = commands.add_parser(
        "optimize", help="Refresh SQLite indexes and query statistics."
    )
    optimize.add_argument("-y", "--yes", action="store_true", help="Skip confirmation.")

    reset = commands.add_parser(
        "reset-librarian",
        help="Reset a Librarian password and revoke existing sessions.",
    )
    reset.add_argument("username")
    recovery = commands.add_parser(
        "recovery-link",
        help="Create a short-lived, single-use password recovery link.",
    )
    recovery.add_argument("username")
    recovery.add_argument(
        "--base-url", default="http://127.0.0.1:8787",
        help="Public InfoMancer address used to build the link.",
    )
    recovery.add_argument(
        "--hours", type=int, default=1,
        help="Link lifetime from 1 to 168 hours (default: 1).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "reset-librarian":
        from .admin_cli import reset_librarian

        return reset_librarian(args.username)
    if args.command == "recovery-link":
        from .admin_cli import create_recovery_link

        return create_recovery_link(args.username, args.base_url, args.hours)
    settings = get_settings()
    try:
        database = _database(settings)
        if args.command == "status":
            return command_status(database, args)
        if args.command == "doctor":
            return command_doctor(database, settings, args)
        if args.command == "scan":
            return command_scan(database, args)
        if args.command == "inspect":
            return command_inspect(database, args)
        if args.command == "export":
            return command_export(database, args)
        if args.command == "logs":
            args.limit = max(1, min(args.limit, 50000))
            return command_logs(database, args)
        if args.command == "backup":
            return command_backup(database, args)
        if args.command == "optimize":
            return command_optimize(database, args)
    except CliError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (OSError, sqlite3.Error) as exc:
        print(
            "ERROR: InfoMancer could not complete the command. The requested "
            f"operation stopped. Technical detail: {exc}",
            file=sys.stderr,
        )
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
