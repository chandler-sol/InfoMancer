from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import Database
from .naming import contained_destination, plex_episode_filename, plex_movie_filename


class RenameProposalError(ValueError):
    pass


class RenameProposalService:
    """Persist filesystem-backed rename snapshots so Review GET stays read-only."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _merged_episode_name(
        names: dict[tuple[int, int], str], season: int, start: int, end: int | None,
    ) -> str:
        final = max(start, end or start)
        return " + ".join(
            name for episode in range(start, final + 1)
            if (name := names.get((season, episode)))
        )

    def _snapshots(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            titles = conn.execute(
                """SELECT t.*,r.path root_path,r.label root_label
                   FROM titles t JOIN roots r ON r.id=t.root_id
                   WHERE t.kind IN ('movie','tv') ORDER BY t.id"""
            ).fetchall()
            files_by_title: dict[int, list] = {}
            for row in conn.execute(
                """SELECT id,title_id,path,filename,extension,season,episode_start,episode_end
                   FROM files ORDER BY title_id,id"""
            ).fetchall():
                files_by_title.setdefault(int(row["title_id"]), []).append(row)
            names_by_title: dict[int, dict[tuple[int, int], str]] = {}
            for row in conn.execute(
                """SELECT title_id,season,episode,name FROM expected_episodes
                   WHERE name IS NOT NULL AND TRIM(name)!=''"""
            ).fetchall():
                names_by_title.setdefault(int(row["title_id"]), {})[
                    (int(row["season"]), int(row["episode"]))
                ] = row["name"]

        snapshots: list[dict[str, Any]] = []
        for title in titles:
            title_id = int(title["id"])
            display_title = title["metadata_title"] or title["title"]
            for file_row in files_by_title.get(title_id, []):
                source = Path(file_row["path"])
                desired = ""
                proposal_kind = "movie" if title["kind"] == "movie" else "episode"
                if title["kind"] == "movie":
                    desired = plex_movie_filename(
                        display_title,
                        title["metadata_year"] or title["year"],
                        file_row["extension"],
                        title["tmdb_id"] or "",
                        title["imdb_id"] or "",
                    )
                else:
                    if file_row["season"] is None or file_row["episode_start"] is None:
                        continue
                    season = int(file_row["season"])
                    start = int(file_row["episode_start"])
                    episode_name = self._merged_episode_name(
                        names_by_title.get(title_id, {}),
                        season, start, file_row["episode_end"],
                    )
                    desired = plex_episode_filename(
                        display_title,
                        title["metadata_year"] or title["year"],
                        season, start, episode_name, file_row["extension"],
                        file_row["episode_end"],
                    )
                if desired == source.name:
                    continue
                status = "active"
                reason = "Ready to rename"
                try:
                    destination = contained_destination(source, desired)
                except ValueError as exc:
                    destination = source
                    status, reason = "blocked", str(exc)
                stat_size = 0
                stat_mtime_ns = 0
                if status == "active":
                    try:
                        stat = source.stat()
                        if not source.is_file():
                            raise OSError("source is not a regular media file")
                        stat_size = int(stat.st_size)
                        stat_mtime_ns = int(stat.st_mtime_ns)
                    except OSError as exc:
                        status, reason = "blocked", f"Source is unavailable: {exc}"
                    if destination.exists():
                        status, reason = (
                            "blocked",
                            "A file already exists at the proposed destination",
                        )
                snapshots.append({
                    "file_id": int(file_row["id"]),
                    "title_id": title_id,
                    "root_id": int(title["root_id"]),
                    "proposal_kind": proposal_kind,
                    "title_name": display_title,
                    "root_label": title["root_label"] or "",
                    "source_path": str(source),
                    "destination_path": str(destination),
                    "source_size": stat_size,
                    "source_mtime_ns": stat_mtime_ns,
                    "status": status,
                    "reason": reason,
                })
        return snapshots

    def refresh_all(self) -> dict[str, int]:
        snapshots = self._snapshots()
        seen_file_ids = {item["file_id"] for item in snapshots}
        with self.database.connect() as conn:
            if seen_file_ids:
                placeholders = ",".join("?" for _ in seen_file_ids)
                conn.execute(
                    f"""UPDATE rename_proposals SET status='resolved',
                         reason='The file now matches its expected name or no longer has a proposal',
                         updated_at=CURRENT_TIMESTAMP,last_checked_at=CURRENT_TIMESTAMP
                         WHERE status IN ('active','blocked','stale')
                           AND file_id NOT IN ({placeholders})""",
                    tuple(sorted(seen_file_ids)),
                )
            else:
                conn.execute(
                    """UPDATE rename_proposals SET status='resolved',
                       reason='The file now matches its expected name or no longer has a proposal',
                       updated_at=CURRENT_TIMESTAMP,last_checked_at=CURRENT_TIMESTAMP
                       WHERE status IN ('active','blocked','stale')"""
                )
            for item in snapshots:
                existing = conn.execute(
                    "SELECT * FROM rename_proposals WHERE file_id=?",
                    (item["file_id"],),
                ).fetchone()
                preserve_dismissed = bool(
                    existing
                    and existing["status"] == "dismissed"
                    and existing["source_path"] == item["source_path"]
                    and existing["destination_path"] == item["destination_path"]
                    and int(existing["source_size"] or 0) == item["source_size"]
                    and int(existing["source_mtime_ns"] or 0) == item["source_mtime_ns"]
                )
                status = "dismissed" if preserve_dismissed else item["status"]
                reason = existing["reason"] if preserve_dismissed else item["reason"]
                conn.execute(
                    """INSERT INTO rename_proposals(
                         file_id,title_id,root_id,proposal_kind,source_path,destination_path,
                         source_size,source_mtime_ns,status,reason,last_checked_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                       ON CONFLICT(file_id) DO UPDATE SET
                         title_id=excluded.title_id,root_id=excluded.root_id,
                         proposal_kind=excluded.proposal_kind,source_path=excluded.source_path,
                         destination_path=excluded.destination_path,
                         source_size=excluded.source_size,source_mtime_ns=excluded.source_mtime_ns,
                         status=excluded.status,reason=excluded.reason,
                         last_checked_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
                    (
                        item["file_id"], item["title_id"], item["root_id"],
                        item["proposal_kind"], item["source_path"],
                        item["destination_path"], item["source_size"],
                        item["source_mtime_ns"], status, reason,
                    ),
                )
            row = conn.execute(
                """SELECT COUNT(*) total,
                          SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active,
                          SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) blocked,
                          SUM(CASE WHEN status='dismissed' THEN 1 ELSE 0 END) dismissed
                   FROM rename_proposals"""
            ).fetchone()
        return {
            "checked": len(snapshots),
            "total": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "blocked": int(row["blocked"] or 0),
            "dismissed": int(row["dismissed"] or 0),
        }

    def list_for_review(self, status: str = "active") -> list[dict[str, Any]]:
        if status == "dismissed":
            condition, params = "p.status='dismissed'", ()
        elif status == "resolved":
            condition, params = "p.status IN ('resolved','applied','stale')", ()
        else:
            condition, params = "p.status IN ('active','blocked')", ()
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT p.*,COALESCE(NULLIF(t.metadata_title,''),t.title) title_name,
                            t.kind title_kind,r.label root_label
                     FROM rename_proposals p
                     JOIN titles t ON t.id=p.title_id
                     JOIN roots r ON r.id=p.root_id
                     WHERE {condition}
                     ORDER BY p.updated_at DESC,p.id DESC""",
                params,
            ).fetchall()
        return [self._view(row) for row in rows]

    def get(self, proposal_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT p.*,COALESCE(NULLIF(t.metadata_title,''),t.title) title_name,
                          t.kind title_kind,r.label root_label,r.path root_path
                   FROM rename_proposals p
                   JOIN titles t ON t.id=p.title_id
                   JOIN roots r ON r.id=p.root_id WHERE p.id=?""",
                (proposal_id,),
            ).fetchone()
        return self._view(row) if row else None

    def dismiss(self, proposal_id: int) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE rename_proposals SET status='dismissed',
                   reason='Dismissed by Librarian',updated_at=CURRENT_TIMESTAMP
                   WHERE id=? AND status IN ('active','blocked')""",
                (proposal_id,),
            )
        return bool(cursor.rowcount)

    def restore(self, proposal_id: int) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE rename_proposals SET status='stale',
                   reason='Refresh rename proposals to revalidate this item',
                   updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='dismissed'""",
                (proposal_id,),
            )
        return bool(cursor.rowcount)

    def apply(self, proposal_id: int) -> dict[str, Any]:
        proposal = self.get(proposal_id)
        if not proposal:
            raise RenameProposalError("That rename proposal no longer exists.")
        if proposal["status"] != "active":
            raise RenameProposalError(
                "That proposal is not currently ready. Refresh rename proposals and review it again."
            )
        source = Path(proposal["source_path"])
        destination = Path(proposal["destination_path"])
        root = Path(proposal["root_path"])
        self._require_inside(source, root)
        self._require_inside(destination, root)
        with self.database.connect() as conn:
            file_row = conn.execute(
                "SELECT path FROM files WHERE id=? AND title_id=?",
                (proposal["file_id"], proposal["title_id"]),
            ).fetchone()
        if not file_row or file_row["path"] != str(source):
            self._mark_stale(proposal_id, "The cataloged source path changed")
            raise RenameProposalError(
                "The file path changed after this proposal was prepared. Refresh rename proposals before applying it."
            )
        try:
            stat = source.stat()
        except OSError as exc:
            self._mark_stale(proposal_id, f"Source is unavailable: {exc}")
            raise RenameProposalError(
                "The source file is no longer available at the reviewed path."
            ) from exc
        if (
            int(stat.st_size) != int(proposal["source_size"] or 0)
            or int(stat.st_mtime_ns) != int(proposal["source_mtime_ns"] or 0)
        ):
            self._mark_stale(proposal_id, "The source file changed after proposal generation")
            raise RenameProposalError(
                "The source file changed after this proposal was prepared. Refresh rename proposals before applying it."
            )
        if destination.exists():
            self._mark_stale(proposal_id, "The destination is now occupied")
            raise RenameProposalError(
                "A file now exists at the proposed destination. Nothing was overwritten."
            )
        try:
            source.rename(destination)
        except OSError as exc:
            raise RenameProposalError(f"The file could not be renamed: {exc}") from exc
        try:
            with self.database.connect() as conn:
                current = conn.execute(
                    "SELECT path FROM files WHERE id=?", (proposal["file_id"],)
                ).fetchone()
                if not current or current["path"] != str(source):
                    raise RenameProposalError(
                        "The catalog changed while the rename was being applied."
                    )
                conn.execute(
                    "UPDATE files SET path=?,filename=? WHERE id=?",
                    (str(destination), destination.name, proposal["file_id"]),
                )
                conn.execute(
                    """UPDATE rename_proposals SET status='applied',reason='Applied',
                       updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (proposal_id,),
                )
        except Exception:
            try:
                destination.rename(source)
            except OSError as rollback_exc:
                raise RenameProposalError(
                    f"The catalog update failed and automatic rename rollback also failed: {rollback_exc}"
                )
            raise
        proposal["status"] = "applied"
        return proposal

    def _mark_stale(self, proposal_id: int, reason: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE rename_proposals SET status='stale',reason=?,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (reason[:500], proposal_id),
            )

    @staticmethod
    def _require_inside(path: Path, root: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
        except (OSError, ValueError) as exc:
            raise RenameProposalError(
                "The recorded rename path is outside its configured source. Nothing was changed."
            ) from exc

    @staticmethod
    def _view(row) -> dict[str, Any]:
        item = dict(row)
        item["source_name"] = Path(item["source_path"]).name
        item["destination_name"] = Path(item["destination_path"]).name
        return item
