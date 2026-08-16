from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import Database


class SeasonFolderError(ValueError):
    pass


class SeasonFolderService:
    """Preview and apply TV episode moves into Plex-style season folders."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def folder_name(season: int) -> str:
        return "Specials" if season == 0 else f"Season {season:02d}"

    def _title_and_rows(self, title_id: int):
        with self.database.connect() as conn:
            title = conn.execute(
                """SELECT t.*,r.path root_path FROM titles t
                   JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                (title_id,),
            ).fetchone()
            if not title:
                raise SeasonFolderError("Title not found.")
            if title["kind"] != "tv":
                raise SeasonFolderError(
                    "Season-folder organization is available for TV series only."
                )
            rows = conn.execute(
                """SELECT id,path,filename,season,episode_start,episode_end
                   FROM files WHERE title_id=?
                   ORDER BY season,episode_start,filename COLLATE NOCASE""",
                (title_id,),
            ).fetchall()
        return title, rows

    def _proposal(self, title, row) -> dict[str, Any] | None:
        if row["season"] is None:
            return None
        title_folder = Path(title["folder_path"])
        root = Path(title["root_path"])
        season = int(row["season"])
        source = Path(row["path"])
        target_folder = title_folder / self.folder_name(season)
        destination = target_folder / source.name
        status = "ready"
        reason = "Ready to move"
        try:
            self._require_inside(title_folder, root, "The show folder")
            self._require_inside(source, root, "The media file")
            self._require_inside(destination, title_folder, "The season destination")
        except SeasonFolderError as exc:
            status, reason = "blocked", str(exc)
        else:
            try:
                same_path = (
                    source.resolve(strict=False) == destination.resolve(strict=False)
                )
            except OSError:
                same_path = source == destination
            if same_path:
                status, reason = (
                    "organized",
                    "Already in the expected season folder",
                )
            elif not source.is_file():
                status, reason = (
                    "blocked",
                    "The cataloged source file is not currently available",
                )
            elif destination.exists():
                status, reason = (
                    "blocked",
                    "A file already exists at the proposed destination",
                )
        return {
            "file_id": int(row["id"]),
            "filename": row["filename"],
            "season": season,
            "episode_start": row["episode_start"],
            "episode_end": row["episode_end"],
            "source": str(source),
            "destination": str(destination),
            "folder": self.folder_name(season),
            "status": status,
            "reason": reason,
        }

    def preview(self, title_id: int) -> dict[str, Any]:
        title, rows = self._title_and_rows(title_id)
        proposals: list[dict[str, Any]] = []
        skipped_unparsed = 0
        for row in rows:
            proposal = self._proposal(title, row)
            if proposal is None:
                skipped_unparsed += 1
                continue
            proposals.append(proposal)
        return {
            "title": dict(title),
            "proposals": proposals,
            "ready": [item for item in proposals if item["status"] == "ready"],
            "blocked": [item for item in proposals if item["status"] == "blocked"],
            "organized": [
                item for item in proposals if item["status"] == "organized"
            ],
            "skipped_unparsed": skipped_unparsed,
        }

    def _current_proposal(self, title_id: int, file_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            title = conn.execute(
                """SELECT t.*,r.path root_path FROM titles t
                   JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                (title_id,),
            ).fetchone()
            row = conn.execute(
                """SELECT id,path,filename,season,episode_start,episode_end
                   FROM files WHERE id=? AND title_id=?""",
                (file_id, title_id),
            ).fetchone()
        if not title or not row:
            return None
        return self._proposal(title, row)

    def _rollback_moves(
        self,
        moved: list[dict[str, Any]],
        created_folders: set[Path],
    ) -> list[str]:
        errors: list[str] = []
        for proposal in reversed(moved):
            source = Path(proposal["source"])
            destination = Path(proposal["destination"])
            if not destination.exists():
                errors.append(
                    f"{destination.name} was no longer at the temporary destination"
                )
                continue
            if source.exists():
                errors.append(
                    f"the original path for {source.name} became occupied during rollback"
                )
                continue
            try:
                destination.rename(source)
            except OSError as exc:
                errors.append(f"could not restore {source.name}: {exc}")
        for folder in sorted(created_folders, key=lambda item: len(item.parts), reverse=True):
            try:
                folder.rmdir()
            except OSError:
                # It is safe to leave a non-empty or otherwise non-removable
                # season folder behind. Never delete contents during rollback.
                pass
        return errors

    def apply(self, title_id: int, selected_file_ids: list[int]) -> list[dict[str, Any]]:
        selected = list(
            dict.fromkeys(
                int(value) for value in selected_file_ids if int(value) > 0
            )
        )
        if not selected:
            raise SeasonFolderError(
                "Select at least one ready episode file to organize."
            )
        preview = self.preview(title_id)
        ready = {item["file_id"]: item for item in preview["ready"]}
        missing = set(selected) - ready.keys()
        if missing:
            raise SeasonFolderError(
                "The preview changed before apply. Refresh the season-folder preview "
                "and review it again."
            )

        moved: list[dict[str, Any]] = []
        created_folders: set[Path] = set()
        try:
            for file_id in selected:
                proposal = ready[file_id]
                source = Path(proposal["source"])
                destination = Path(proposal["destination"])

                # Revalidate this exact file immediately before mutation instead
                # of rescanning every episode in the title for each selected row.
                current = self._current_proposal(title_id, file_id)
                if (
                    not current
                    or current["status"] != "ready"
                    or current["source"] != str(source)
                    or current["destination"] != str(destination)
                ):
                    raise SeasonFolderError(
                        f"Stopped before moving {source.name} because its filesystem "
                        "or catalog state changed. Nothing from this batch was kept."
                    )

                target_folder = destination.parent
                if not target_folder.exists():
                    try:
                        target_folder.mkdir()
                        created_folders.add(target_folder)
                    except OSError as exc:
                        raise SeasonFolderError(
                            f"Could not create {target_folder.name}: {exc}"
                        ) from exc
                elif not target_folder.is_dir():
                    raise SeasonFolderError(
                        f"Cannot organize {source.name} because {target_folder} is not "
                        "a folder."
                    )

                # Check again after any directory creation. This protects the
                # normal collision case and narrows the race window before move.
                if destination.exists():
                    raise SeasonFolderError(
                        f"Stopped before moving {source.name} because a file appeared "
                        "at the proposed destination. Nothing from this batch was kept."
                    )
                try:
                    source.rename(destination)
                except OSError as exc:
                    raise SeasonFolderError(
                        f"Could not move {source.name}: {exc}"
                    ) from exc
                moved.append(proposal)

            # Update all catalog paths in one transaction only after every
            # filesystem move succeeds. Any DB failure rolls back as a unit.
            with self.database.connect() as conn:
                for proposal in moved:
                    row = conn.execute(
                        "SELECT path FROM files WHERE id=? AND title_id=?",
                        (proposal["file_id"], title_id),
                    ).fetchone()
                    if not row or row["path"] != proposal["source"]:
                        raise SeasonFolderError(
                            f"Stopped because the catalog entry for "
                            f"{Path(proposal['source']).name} changed during apply."
                        )
                    destination = Path(proposal["destination"])
                    conn.execute(
                        "UPDATE files SET path=?,filename=? WHERE id=?",
                        (
                            str(destination),
                            destination.name,
                            proposal["file_id"],
                        ),
                    )
        except Exception as exc:
            rollback_errors = self._rollback_moves(moved, created_folders)
            if rollback_errors:
                details = "; ".join(rollback_errors[:3])
                raise SeasonFolderError(
                    "Season-folder organization stopped, and automatic rollback was "
                    f"incomplete: {details}. Review these paths before retrying."
                ) from exc
            if isinstance(exc, SeasonFolderError):
                raise
            raise SeasonFolderError(
                f"Season-folder organization stopped safely: {exc}"
            ) from exc

        return moved

    @staticmethod
    def _require_inside(path: Path, parent: Path, label: str) -> None:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except (OSError, ValueError) as exc:
            raise SeasonFolderError(
                f"{label} is outside the configured library boundary. Nothing was changed."
            ) from exc
