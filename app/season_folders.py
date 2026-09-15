from __future__ import annotations

import os
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
                same_path = source.resolve(strict=False) == destination.resolve(strict=False)
            except OSError:
                same_path = source == destination
            if same_path:
                status, reason = "organized", "Already in the expected season folder"
            elif not source.is_file():
                status, reason = "blocked", "The cataloged source file is not currently available"
            elif os.path.lexists(destination):
                status, reason = "blocked", "A file already exists at the proposed destination"
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
            "organized": [item for item in proposals if item["status"] == "organized"],
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
        created_folders: dict[Path, tuple[str, int, int]],
        root: Path,
        title_folder: Path,
        root_identity: tuple[str, int, int],
        title_identity: tuple[str, int, int],
    ) -> list[str]:
        errors: list[str] = []
        try:
            self._require_inside(title_folder, root, "The show folder")
            if self._path_identity(root) != root_identity or self._path_identity(title_folder) != title_identity:
                raise SeasonFolderError("the configured library or show-folder identity changed")
        except (OSError, SeasonFolderError) as exc:
            return [f"rollback boundary changed: {exc}"]

        for proposal in reversed(moved):
            source = Path(proposal["source"])
            destination = Path(proposal["destination"])
            try:
                self._require_inside(source, root, "The rollback source")
                self._require_inside(destination, title_folder, "The rollback destination")
                if self._path_identity(source.parent) != proposal["_source_parent_identity"]:
                    raise SeasonFolderError("the original parent directory changed")
                if self._path_identity(destination.parent) != proposal["_destination_parent_identity"]:
                    raise SeasonFolderError("the season directory changed")
                if os.path.lexists(source):
                    raise SeasonFolderError(
                        f"the original path for {source.name} became occupied during rollback"
                    )
                if not destination.is_file() or self._file_identity(destination) != proposal["_moved_identity"]:
                    raise SeasonFolderError(
                        f"{destination.name} is no longer the file moved by this operation"
                    )
                destination.rename(source)
            except (OSError, SeasonFolderError) as exc:
                errors.append(f"could not safely restore {source.name}: {exc}")

        for folder, expected_identity in sorted(
            created_folders.items(), key=lambda item: len(item[0].parts), reverse=True
        ):
            try:
                self._require_inside(folder, title_folder, "The created season folder")
                if self._path_identity(root) != root_identity or self._path_identity(title_folder) != title_identity:
                    continue
                if not folder.is_dir() or self._path_identity(folder) != expected_identity:
                    continue
                folder.rmdir()
            except (OSError, SeasonFolderError):
                pass
        return errors

    def apply(self, title_id: int, selected_file_ids: list[int]) -> list[dict[str, Any]]:
        selected = list(dict.fromkeys(int(value) for value in selected_file_ids if int(value) > 0))
        if not selected:
            raise SeasonFolderError("Select at least one ready episode file to organize.")
        preview = self.preview(title_id)
        ready = {item["file_id"]: item for item in preview["ready"]}
        missing = set(selected) - ready.keys()
        if missing:
            raise SeasonFolderError(
                "The preview changed before apply. Refresh the season-folder preview and review it again."
            )
        root = Path(preview["title"]["root_path"])
        title_folder = Path(preview["title"]["folder_path"])
        self._require_inside(title_folder, root, "The show folder")
        try:
            root_identity = self._path_identity(root)
            title_identity = self._path_identity(title_folder)
        except OSError as exc:
            raise SeasonFolderError(
                "The configured source or show folder is unavailable. Nothing was changed."
            ) from exc

        moved: list[dict[str, Any]] = []
        created_folders: dict[Path, tuple[str, int, int]] = {}
        try:
            for file_id in selected:
                proposal = ready[file_id]
                source = Path(proposal["source"])
                destination = Path(proposal["destination"])

                current = self._current_proposal(title_id, file_id)
                if (
                    not current
                    or current["status"] != "ready"
                    or current["source"] != str(source)
                    or current["destination"] != str(destination)
                ):
                    raise SeasonFolderError(
                        f"Stopped before moving {source.name} because its filesystem or catalog state changed. Nothing from this batch was kept."
                    )

                if self._path_identity(root) != root_identity or self._path_identity(title_folder) != title_identity:
                    raise SeasonFolderError(
                        "Stopped because the configured source or show folder changed during apply. Nothing from this batch was kept."
                    )

                target_folder = destination.parent
                if not target_folder.exists():
                    try:
                        target_folder.mkdir()
                        created_folders[target_folder] = self._path_identity(target_folder)
                    except OSError as exc:
                        raise SeasonFolderError(f"Could not create {target_folder.name}: {exc}") from exc
                elif not target_folder.is_dir():
                    raise SeasonFolderError(
                        f"Cannot organize {source.name} because {target_folder} is not a folder."
                    )

                self._require_inside(title_folder, root, "The show folder")
                self._require_inside(source, root, "The media file")
                self._require_inside(destination, title_folder, "The season destination")
                if self._path_identity(root) != root_identity or self._path_identity(title_folder) != title_identity:
                    raise SeasonFolderError(
                        "Stopped because the library boundary changed before the move. Nothing from this batch was kept."
                    )
                if os.path.lexists(destination):
                    raise SeasonFolderError(
                        f"Stopped before moving {source.name} because a file appeared at the proposed destination. Nothing from this batch was kept."
                    )

                source_parent_identity = self._path_identity(source.parent)
                destination_parent_identity = self._path_identity(destination.parent)
                try:
                    source.rename(destination)
                except OSError as exc:
                    raise SeasonFolderError(f"Could not move {source.name}: {exc}") from exc
                applied = dict(proposal)
                applied["_source_parent_identity"] = source_parent_identity
                applied["_destination_parent_identity"] = destination_parent_identity
                applied["_moved_identity"] = self._file_identity(destination)
                moved.append(applied)

            with self.database.connect() as conn:
                for proposal in moved:
                    cursor = conn.execute(
                        """UPDATE files SET path=?,filename=?
                           WHERE id=? AND title_id=? AND path=?""",
                        (
                            proposal["destination"],
                            Path(proposal["destination"]).name,
                            proposal["file_id"],
                            title_id,
                            proposal["source"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SeasonFolderError(
                            f"Stopped because the catalog entry for {Path(proposal['source']).name} changed during apply."
                        )
        except Exception as exc:
            rollback_errors = self._rollback_moves(
                moved,
                created_folders,
                root,
                title_folder,
                root_identity,
                title_identity,
            )
            if rollback_errors:
                details = "; ".join(rollback_errors[:3])
                raise SeasonFolderError(
                    "Season-folder organization stopped, and automatic rollback was incomplete: "
                    f"{details}. Review these paths before retrying."
                ) from exc
            if isinstance(exc, SeasonFolderError):
                raise
            raise SeasonFolderError(f"Season-folder organization stopped safely: {exc}") from exc

        for proposal in moved:
            proposal.pop("_source_parent_identity", None)
            proposal.pop("_destination_parent_identity", None)
            proposal.pop("_moved_identity", None)
        return moved

    @staticmethod
    def _require_inside(path: Path, parent: Path, label: str) -> None:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        except (OSError, ValueError) as exc:
            raise SeasonFolderError(
                f"{label} is outside the configured library boundary. Nothing was changed."
            ) from exc

    @staticmethod
    def _path_identity(path: Path) -> tuple[str, int, int]:
        resolved = path.resolve(strict=False)
        stat = resolved.stat()
        return (str(resolved), int(stat.st_dev), int(stat.st_ino))

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int, int, int]:
        stat = path.stat()
        return (int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))
