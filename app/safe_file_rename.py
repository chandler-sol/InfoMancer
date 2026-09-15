from __future__ import annotations

import os
import stat
from pathlib import Path

from .catalog_mutation import database_identity, root_mutation_lock, title_mutation_lock
from .db import Database


class SafeFileRenameError(ValueError):
    pass


class SafeFileRenameService:
    """Perform catalog-backed media renames with fail-closed live revalidation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _require_inside(path: Path, root: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
        except (OSError, ValueError) as exc:
            raise SafeFileRenameError(
                "The media path is outside its configured source or cannot be verified. Nothing was changed."
            ) from exc

    @staticmethod
    def _same_file(left: Path, right: Path) -> bool:
        try:
            return left.exists() and right.exists() and os.path.samefile(left, right)
        except OSError:
            return False

    @staticmethod
    def _path_identity(path: Path) -> tuple[str, int, int]:
        """Identify the effective directory at a stable pathname."""
        resolved = path.resolve(strict=False)
        details = resolved.stat()
        return (str(resolved), int(details.st_dev), int(details.st_ino))

    @staticmethod
    def _entry_identity(path: Path) -> tuple[int, int, int]:
        """Identify the directory entry that rename() will move without following symlinks."""
        details = path.lstat()
        return (
            int(details.st_dev),
            int(details.st_ino),
            int(stat.S_IFMT(details.st_mode)),
        )

    def _capture_move_identity(
        self,
        source: Path,
        target: Path,
        root: Path,
        noun: str,
    ) -> tuple[
        tuple[str, int, int],
        tuple[str, int, int],
        tuple[str, int, int],
        tuple[int, int, int],
    ]:
        try:
            root_identity = self._path_identity(root)
            source_parent_identity = self._path_identity(source.parent)
            target_parent_identity = self._path_identity(target.parent)
            moved_identity = self._entry_identity(source)
        except OSError as exc:
            raise SafeFileRenameError(
                f"The {noun} or its library boundary changed before the rename could begin. Nothing was changed."
            ) from exc
        return (
            root_identity,
            source_parent_identity,
            target_parent_identity,
            moved_identity,
        )

    def _revalidate_before_move(
        self,
        source: Path,
        target: Path,
        root: Path,
        root_identity: tuple[str, int, int],
        source_parent_identity: tuple[str, int, int],
        target_parent_identity: tuple[str, int, int],
        moved_identity: tuple[int, int, int],
        noun: str,
    ) -> None:
        try:
            if self._path_identity(root) != root_identity:
                raise SafeFileRenameError(
                    f"The configured source changed before the {noun} rename could begin. Nothing was changed."
                )
            if self._path_identity(source.parent) != source_parent_identity:
                raise SafeFileRenameError(
                    f"The original parent changed before the {noun} rename could begin. Nothing was changed."
                )
            if self._path_identity(target.parent) != target_parent_identity:
                raise SafeFileRenameError(
                    f"The destination parent changed before the {noun} rename could begin. Nothing was changed."
                )
            if self._entry_identity(source) != moved_identity:
                raise SafeFileRenameError(
                    f"The {noun} changed before the rename could begin. Nothing was changed."
                )
        except OSError as exc:
            raise SafeFileRenameError(
                f"The {noun} or its library boundary became unavailable before the rename could begin. Nothing was changed."
            ) from exc

    def _validate_rollback_identity(
        self,
        source: Path,
        target: Path,
        root: Path,
        root_identity: tuple[str, int, int],
        source_parent_identity: tuple[str, int, int],
        target_parent_identity: tuple[str, int, int],
        moved_identity: tuple[int, int, int],
        noun: str,
        expect_directory: bool,
    ) -> None:
        """Fail closed unless rollback still targets the exact pre-move filesystem objects."""
        try:
            if self._path_identity(root) != root_identity:
                raise SafeFileRenameError(
                    f"The {noun} was renamed but the catalog update failed, and the configured source identity changed before rollback. InfoMancer refused to move anything automatically. Review both paths before retrying."
                )
            if os.path.lexists(source):
                raise SafeFileRenameError(
                    f"The {noun} was renamed but the catalog update failed, and another entry appeared at the original path before rollback. InfoMancer refused to overwrite it. Review both paths before retrying."
                )
            self._require_inside(target, root)
            self._require_inside(source, root)
            if self._path_identity(source.parent) != source_parent_identity:
                raise SafeFileRenameError(
                    f"The {noun} was renamed but the catalog update failed, and the original parent identity changed before rollback. InfoMancer refused to move anything automatically. Review both paths before retrying."
                )
            if self._path_identity(target.parent) != target_parent_identity:
                raise SafeFileRenameError(
                    f"The {noun} was renamed but the catalog update failed, and the destination parent identity changed before rollback. InfoMancer refused to move anything automatically. Review both paths before retrying."
                )
            if expect_directory:
                target_has_expected_type = target.is_dir()
            else:
                target_has_expected_type = target.is_file()
            if not target_has_expected_type or self._entry_identity(target) != moved_identity:
                raise SafeFileRenameError(
                    f"The {noun} was renamed but the catalog update failed, and the destination no longer contains the exact object moved by this operation. InfoMancer refused to move it automatically. Review both paths before retrying."
                )
        except SafeFileRenameError:
            raise
        except OSError as exc:
            raise SafeFileRenameError(
                f"The {noun} was renamed but the catalog update failed, and the filesystem identity could not be verified before rollback. InfoMancer refused to move anything automatically. Review both paths before retrying."
            ) from exc

    def _validate_file_move(self, source: Path, destination: Path, root: Path) -> None:
        self._require_inside(source, root)
        if os.path.lexists(destination) and not self._same_file(source, destination):
            raise SafeFileRenameError(
                f"Another file already exists at the rename destination: {destination}"
            )
        self._require_inside(destination, root)
        if not source.is_file():
            raise SafeFileRenameError(
                "The cataloged media file is no longer present at the expected path. Nothing was changed."
            )
        if not destination.parent.is_dir():
            raise SafeFileRenameError(
                "The rename destination folder is no longer available. Nothing was changed."
            )

    def rename_file(
        self, file_id: int, expected_source: Path | str, destination: Path | str,
    ) -> tuple[Path, Path]:
        source = Path(expected_source)
        target = Path(destination)
        if source == target:
            return source, target

        with self.database.connect() as conn:
            owner = conn.execute(
                """SELECT f.title_id,t.root_id FROM files f
                   JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
        if not owner:
            raise SafeFileRenameError(
                "The cataloged media file no longer exists. Refresh and try again."
            )

        database_key = database_identity(self.database.path)
        with title_mutation_lock(database_key, int(owner["title_id"])):
            with root_mutation_lock(database_key, int(owner["root_id"])):
                return self._rename_file_locked(file_id, source, target)

    def _rename_file_locked(
        self, file_id: int, source: Path, target: Path,
    ) -> tuple[Path, Path]:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT f.path,f.title_id,t.folder_path,r.path root_path
                   FROM files f JOIN titles t ON t.id=f.title_id
                   JOIN roots r ON r.id=t.root_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
        if not row or row["path"] != str(source):
            raise SafeFileRenameError(
                "The cataloged media path changed after this rename was prepared. Refresh and try again."
            )
        root = Path(row["root_path"])
        self._validate_file_move(source, target, root)
        self._validate_file_move(source, target, root)
        (
            root_identity,
            source_parent_identity,
            target_parent_identity,
            moved_identity,
        ) = self._capture_move_identity(source, target, root, "file")
        self._revalidate_before_move(
            source,
            target,
            root,
            root_identity,
            source_parent_identity,
            target_parent_identity,
            moved_identity,
            "file",
        )

        try:
            source.rename(target)
        except OSError as exc:
            raise SafeFileRenameError(f"The media file could not be renamed: {exc}") from exc

        try:
            if self._entry_identity(target) != moved_identity:
                raise SafeFileRenameError(
                    "The media file was renamed, but its filesystem identity changed before the catalog update."
                )
            with self.database.connect() as conn:
                cursor = conn.execute(
                    "UPDATE files SET path=?,filename=? WHERE id=? AND path=?",
                    (str(target), target.name, file_id, str(source)),
                )
                if cursor.rowcount != 1:
                    raise SafeFileRenameError(
                        "The catalog changed while the media rename was being applied."
                    )
                if row["folder_path"] == str(source):
                    title_cursor = conn.execute(
                        """UPDATE titles SET folder_path=?,updated_at=CURRENT_TIMESTAMP
                           WHERE id=? AND folder_path=?""",
                        (str(target), row["title_id"], str(source)),
                    )
                    if title_cursor.rowcount != 1:
                        raise SafeFileRenameError(
                            "The cataloged title changed while the media rename was being applied."
                        )
        except Exception as exc:
            try:
                self._validate_rollback_identity(
                    source,
                    target,
                    root,
                    root_identity,
                    source_parent_identity,
                    target_parent_identity,
                    moved_identity,
                    "file",
                    False,
                )
                target.rename(source)
            except SafeFileRenameError:
                raise
            except OSError as rollback_exc:
                raise SafeFileRenameError(
                    f"The catalog update failed and automatic rename rollback also failed: {rollback_exc}"
                ) from rollback_exc
            raise SafeFileRenameError(
                "The catalog update failed, so InfoMancer restored the media file to its original path. Nothing was left partially renamed."
            ) from exc
        return source, target

    def rename_folder(
        self, title_id: int, expected_source: Path | str, destination: Path | str,
    ) -> tuple[Path, Path]:
        source = Path(expected_source)
        target = Path(destination)
        if source == target:
            return source, target

        with self.database.connect() as conn:
            owner = conn.execute(
                "SELECT root_id FROM titles WHERE id=?", (title_id,),
            ).fetchone()
        if not owner:
            raise SafeFileRenameError(
                "The cataloged show no longer exists. Refresh and try again."
            )

        database_key = database_identity(self.database.path)
        with title_mutation_lock(database_key, title_id):
            with root_mutation_lock(database_key, int(owner["root_id"])):
                return self._rename_folder_locked(title_id, source, target)

    def _rename_folder_locked(
        self, title_id: int, source: Path, target: Path,
    ) -> tuple[Path, Path]:
        with self.database.connect() as conn:
            title = conn.execute(
                """SELECT t.folder_path,r.path root_path
                   FROM titles t JOIN roots r ON r.id=t.root_id WHERE t.id=?""",
                (title_id,),
            ).fetchone()
            file_rows = conn.execute(
                "SELECT id,path FROM files WHERE title_id=? ORDER BY id", (title_id,),
            ).fetchall()
        if not title or title["folder_path"] != str(source):
            raise SafeFileRenameError(
                "The cataloged show folder changed after this rename was prepared. Refresh and try again."
            )
        root = Path(title["root_path"])
        self._require_inside(source, root)
        if os.path.lexists(target) and not self._same_file(source, target):
            raise SafeFileRenameError(
                f"Another folder already exists at the rename destination: {target}"
            )
        self._require_inside(target, root)
        if not source.is_dir():
            raise SafeFileRenameError(
                "The cataloged show folder is no longer present at the expected path. Nothing was changed."
            )
        if not target.parent.is_dir():
            raise SafeFileRenameError(
                "The rename destination parent is no longer available. Nothing was changed."
            )

        relative_paths: list[tuple[int, str, Path]] = []
        expected_membership: list[tuple[int, str]] = []
        for file_row in file_rows:
            file_id = int(file_row["id"])
            original_path = str(file_row["path"])
            try:
                relative = Path(original_path).relative_to(source)
            except ValueError as exc:
                raise SafeFileRenameError(
                    "A cataloged media file is no longer inside the show folder. Nothing was changed."
                ) from exc
            relative_paths.append((file_id, original_path, relative))
            expected_membership.append((file_id, original_path))

        self._require_inside(source, root)
        if os.path.lexists(target) and not self._same_file(source, target):
            raise SafeFileRenameError(
                f"Another folder appeared at the rename destination: {target}"
            )
        self._require_inside(target, root)
        if not source.is_dir():
            raise SafeFileRenameError(
                "The show folder changed before the rename could begin. Nothing was changed."
            )
        if not target.parent.is_dir():
            raise SafeFileRenameError(
                "The rename destination parent changed before the rename could begin. Nothing was changed."
            )

        (
            root_identity,
            source_parent_identity,
            target_parent_identity,
            moved_identity,
        ) = self._capture_move_identity(source, target, root, "folder")
        self._revalidate_before_move(
            source,
            target,
            root,
            root_identity,
            source_parent_identity,
            target_parent_identity,
            moved_identity,
            "folder",
        )

        try:
            source.rename(target)
        except OSError as exc:
            raise SafeFileRenameError(f"The show folder could not be renamed: {exc}") from exc

        try:
            if self._entry_identity(target) != moved_identity:
                raise SafeFileRenameError(
                    "The show folder was renamed, but its filesystem identity changed before the catalog update."
                )
            with self.database.connect() as conn:
                # Hold SQLite's write reservation from complete-membership validation
                # through the catalog rewrite. A writer that bypasses the in-process
                # root lease therefore cannot insert an uncaptured row in this window.
                conn.execute("BEGIN IMMEDIATE")
                current_membership = [
                    (int(row["id"]), str(row["path"]))
                    for row in conn.execute(
                        "SELECT id,path FROM files WHERE title_id=? ORDER BY id", (title_id,),
                    ).fetchall()
                ]
                current_title = conn.execute(
                    "SELECT folder_path FROM titles WHERE id=?", (title_id,),
                ).fetchone()
                if (
                    current_title is None
                    or current_title["folder_path"] != str(source)
                    or current_membership != expected_membership
                ):
                    raise SafeFileRenameError(
                        "The catalog membership changed while the show-folder rename was being applied."
                    )

                for file_id, original_path, relative in relative_paths:
                    cursor = conn.execute(
                        "UPDATE files SET path=? WHERE id=? AND path=?",
                        (str(target / relative), file_id, original_path),
                    )
                    if cursor.rowcount != 1:
                        raise SafeFileRenameError(
                            "A cataloged media path changed while the show-folder rename was being applied."
                        )
                title_cursor = conn.execute(
                    """UPDATE titles SET folder_path=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND folder_path=?""",
                    (str(target), title_id, str(source)),
                )
                if title_cursor.rowcount != 1:
                    raise SafeFileRenameError(
                        "The catalog changed while the show-folder rename was being applied."
                    )
        except Exception as exc:
            try:
                self._validate_rollback_identity(
                    source,
                    target,
                    root,
                    root_identity,
                    source_parent_identity,
                    target_parent_identity,
                    moved_identity,
                    "folder",
                    True,
                )
                target.rename(source)
            except SafeFileRenameError:
                raise
            except OSError as rollback_exc:
                raise SafeFileRenameError(
                    f"The catalog update failed and automatic folder rollback also failed: {rollback_exc}"
                ) from rollback_exc
            raise SafeFileRenameError(
                "The catalog update failed, so InfoMancer restored the show folder to its original path. Nothing was left partially renamed."
            ) from exc
        return source, target
