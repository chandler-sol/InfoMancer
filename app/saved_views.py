from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode

from .db import Database


class SavedViewError(ValueError):
    pass


class SavedViewService:
    ALLOWED_PATHS = {"/library", "/movies", "/shows"}
    ALLOWED_SORTS = {
        "title", "release_new", "release_old", "rating", "personal_rating",
        "date_added", "runtime", "resolution", "bitrate", "file_size",
        "favorites", "random",
    }
    MAX_VIEWS = 50
    MAX_PINNED = 8

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(name.strip().split())[:60]
        if not cleaned:
            raise SavedViewError("Enter a name for this saved view.")
        return cleaned

    @classmethod
    def normalize_target(cls, path: str, query_string: str) -> tuple[str, str]:
        target_path = path if path in cls.ALLOWED_PATHS else "/library"
        try:
            raw = dict(parse_qsl(query_string, keep_blank_values=False, max_num_fields=30))
        except ValueError as exc:
            raise SavedViewError("That library view contains too many filter values.") from exc

        normalized: dict[str, str] = {}
        q = str(raw.get("q", "")).strip()[:200]
        if q:
            normalized["q"] = q
        letter = str(raw.get("letter", "")).upper()
        if letter == "#" or (len(letter) == 1 and letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            normalized["letter"] = letter
        for key, maximum in (("genre", 100), ("title_type", 100), ("person_name", 200)):
            value = str(raw.get(key, "")).strip()[:maximum]
            if value:
                normalized[key] = value
        root = str(raw.get("root", ""))
        if root.isdigit() and int(root) > 0:
            normalized["root"] = str(int(root))
        person = str(raw.get("person", ""))
        if re.fullmatch(r"nm\d+", person):
            normalized["person"] = person
        credit_role = str(raw.get("credit_role", ""))
        if credit_role in {"actor", "director", "writer"}:
            normalized["credit_role"] = credit_role
        match = str(raw.get("match", ""))
        if match in {"matched", "unmatched"}:
            normalized["match"] = match
        gaps = str(raw.get("gaps", ""))
        if target_path != "/movies" and gaps in {"missing", "complete"}:
            normalized["gaps"] = gaps
        if raw.get("favorite") == "favorites":
            normalized["favorite"] = "favorites"
        tag = str(raw.get("tag", ""))
        if tag.isdigit() and int(tag) > 0:
            normalized["tag"] = str(int(tag))
        sort = str(raw.get("sort", ""))
        if sort in cls.ALLOWED_SORTS and sort != "title":
            normalized["sort"] = sort
        return target_path, urlencode(normalized)

    @staticmethod
    def _view(row) -> dict:
        item = dict(row)
        item["href"] = item["path"] + (f'?{item["query_string"]}' if item["query_string"] else "")
        item["pinned"] = bool(item["pinned"])
        return item

    def list_for_user(self, user_id: int, *, pinned_only: bool = False) -> list[dict]:
        if user_id <= 0:
            return []
        where = " AND pinned=1" if pinned_only else ""
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT id,user_id,name,path,query_string,pinned,created_at,updated_at
                    FROM user_saved_views WHERE user_id=?{where}
                    ORDER BY pinned DESC,name COLLATE NOCASE,id""",
                (user_id,),
            ).fetchall()
        return [self._view(row) for row in rows]

    def save(
        self, user_id: int, name: str, path: str, query_string: str, *, pinned: bool = False,
    ) -> tuple[dict, bool]:
        if user_id <= 0:
            raise SavedViewError("Saved views require a signed-in account.")
        cleaned = self._clean_name(name)
        target_path, target_query = self.normalize_target(path, query_string)
        with self.database.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM user_saved_views WHERE user_id=? AND name=? COLLATE NOCASE",
                (user_id, cleaned),
            ).fetchone()
            if not existing:
                total = int(conn.execute(
                    "SELECT COUNT(*) FROM user_saved_views WHERE user_id=?", (user_id,)
                ).fetchone()[0])
                if total >= self.MAX_VIEWS:
                    raise SavedViewError(
                        f"You can save up to {self.MAX_VIEWS} Library views. Delete one before saving another."
                    )
            if pinned and not (existing and existing["pinned"]):
                pinned_count = int(conn.execute(
                    "SELECT COUNT(*) FROM user_saved_views WHERE user_id=? AND pinned=1",
                    (user_id,),
                ).fetchone()[0])
                if pinned_count >= self.MAX_PINNED:
                    raise SavedViewError(
                        f"Pin up to {self.MAX_PINNED} saved views. Unpin one before adding another."
                    )
            if existing:
                conn.execute(
                    """UPDATE user_saved_views SET name=?,path=?,query_string=?,pinned=?,
                         updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?""",
                    (cleaned, target_path, target_query, int(pinned), existing["id"], user_id),
                )
                view_id = int(existing["id"])
                created = False
            else:
                view_id = int(conn.execute(
                    """INSERT INTO user_saved_views(user_id,name,path,query_string,pinned)
                       VALUES (?,?,?,?,?)""",
                    (user_id, cleaned, target_path, target_query, int(pinned)),
                ).lastrowid)
                created = True
            row = conn.execute(
                "SELECT * FROM user_saved_views WHERE id=? AND user_id=?", (view_id, user_id)
            ).fetchone()
        return self._view(row), created

    def rename(self, user_id: int, view_id: int, name: str) -> dict:
        cleaned = self._clean_name(name)
        try:
            with self.database.connect() as conn:
                result = conn.execute(
                    """UPDATE user_saved_views SET name=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND user_id=?""",
                    (cleaned, view_id, user_id),
                )
                if not result.rowcount:
                    raise SavedViewError("That saved view no longer exists.")
                row = conn.execute(
                    "SELECT * FROM user_saved_views WHERE id=? AND user_id=?",
                    (view_id, user_id),
                ).fetchone()
        except Exception as exc:
            if exc.__class__.__name__ == "IntegrityError":
                raise SavedViewError(f'A saved view named "{cleaned}" already exists.') from exc
            raise
        return self._view(row)

    def toggle_pin(self, user_id: int, view_id: int) -> dict:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_saved_views WHERE id=? AND user_id=?",
                (view_id, user_id),
            ).fetchone()
            if not row:
                raise SavedViewError("That saved view no longer exists.")
            pinned = not bool(row["pinned"])
            if pinned:
                pinned_count = int(conn.execute(
                    "SELECT COUNT(*) FROM user_saved_views WHERE user_id=? AND pinned=1",
                    (user_id,),
                ).fetchone()[0])
                if pinned_count >= self.MAX_PINNED:
                    raise SavedViewError(
                        f"Pin up to {self.MAX_PINNED} saved views. Unpin one before adding another."
                    )
            conn.execute(
                "UPDATE user_saved_views SET pinned=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(pinned), view_id),
            )
            updated = conn.execute("SELECT * FROM user_saved_views WHERE id=?", (view_id,)).fetchone()
        return self._view(updated)

    def delete(self, user_id: int, view_id: int) -> str:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT name FROM user_saved_views WHERE id=? AND user_id=?",
                (view_id, user_id),
            ).fetchone()
            if not row:
                raise SavedViewError("That saved view no longer exists.")
            conn.execute(
                "DELETE FROM user_saved_views WHERE id=? AND user_id=?", (view_id, user_id)
            )
        return str(row["name"])
