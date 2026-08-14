from __future__ import annotations

import re
from typing import Any

from .db import Database


EDITION_PATTERNS = (
    (r"\b(?:director'?s[ ._-]*cut)\b", "Director's Cut"),
    (r"\bextended(?:[ ._-]*(?:cut|edition))?\b", "Extended Edition"),
    (r"\btheatrical(?:[ ._-]*cut)?\b", "Theatrical Cut"),
    (r"\bfinal[ ._-]*cut\b", "Final Cut"),
    (r"\bunrated\b", "Unrated"),
    (r"\bremaster(?:ed)?\b", "Remastered"),
    (r"\bspecial[ ._-]*edition\b", "Special Edition"),
    (r"\bcriterion\b", "Criterion Collection"),
)


def clean_label(value: str, *, limit: int = 80) -> str:
    return " ".join(value.strip().split())[:limit]


def infer_edition(filename: str) -> str:
    candidate = re.sub(r"\.[^.]{2,5}$", "", filename, flags=re.IGNORECASE)
    for pattern, label in EDITION_PATTERNS:
        if re.search(pattern, candidate, re.IGNORECASE):
            return label
    return ""


def infer_version(file: dict[str, Any]) -> str:
    filename = str(file.get("filename") or "")
    width = int(file.get("width") or 0)
    height = int(file.get("height") or 0)
    resolution = ""
    if width >= 3800 or height >= 2000 or re.search(r"\b2160p\b", filename, re.I):
        resolution = "4K"
    elif height >= 1400 or re.search(r"\b1440p\b", filename, re.I):
        resolution = "1440p"
    elif height >= 1000 or re.search(r"\b1080[pi]\b", filename, re.I):
        resolution = "1080p"
    elif height >= 700 or re.search(r"\b720p\b", filename, re.I):
        resolution = "720p"
    elif height:
        resolution = f"{height}p"

    dynamic_range = clean_label(str(file.get("dynamic_range") or ""), limit=24).upper()
    if dynamic_range == "SDR":
        dynamic_range = ""
    source = ""
    for pattern, label in (
        (r"\bremux\b", "REMUX"),
        (r"\bweb[ ._-]?dl\b", "WEB-DL"),
        (r"\b(?:blu[ ._-]?ray|bdrip|bdremux)\b", "Blu-ray"),
        (r"\bhdtv\b", "HDTV"),
    ):
        if re.search(pattern, filename, re.I):
            source = label
            break
    return " ".join(part for part in (resolution, dynamic_range, source) if part)


def identity(file: dict[str, Any]) -> tuple[str, str] | None:
    if not int(file.get("identity_confirmed") or 0):
        return None
    edition = clean_label(str(file.get("edition_name") or "")).casefold()
    version = clean_label(str(file.get("version_name") or "")).casefold()
    return (edition, version) if edition or version else None


def same_playable_item(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if int(left["title_id"]) != int(right["title_id"]):
        return False
    if left.get("kind") != "tv":
        return True
    if left.get("season") != right.get("season"):
        return False
    left_start = left.get("episode_start")
    right_start = right.get("episode_start")
    if left_start is None or right_start is None:
        return False
    left_end = left.get("episode_end") or left_start
    right_end = right.get("episode_end") or right_start
    return max(int(left_start), int(right_start)) <= min(int(left_end), int(right_end))


class EditionVersionService:
    def __init__(self, database: Database):
        self.database = database

    def file(self, file_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT f.*,t.kind,COALESCE(NULLIF(t.metadata_title,''),t.title) title_name
                   FROM files f JOIN titles t ON t.id=f.title_id WHERE f.id=?""",
                (file_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["suggested_edition"] = infer_edition(result["filename"])
        result["suggested_version"] = infer_version(result)
        return result

    def save(
        self, file_id: int, *, edition_name: str, version_name: str,
        preferred: bool,
    ) -> dict[str, Any] | None:
        current = self.file(file_id)
        if not current:
            return None
        edition = clean_label(edition_name)
        version = clean_label(version_name)
        confirmed = bool(edition or version or preferred)
        with self.database.connect() as conn:
            if preferred:
                siblings = [dict(row) for row in conn.execute(
                    """SELECT f.*,t.kind FROM files f JOIN titles t ON t.id=f.title_id
                       WHERE f.title_id=?""", (current["title_id"],),
                )]
                other_ids = [
                    int(row["id"]) for row in siblings
                    if int(row["id"]) != file_id and same_playable_item(current, row)
                ]
                if other_ids:
                    conn.execute(
                        f"UPDATE files SET version_preferred=0 WHERE id IN ({','.join('?' for _ in other_ids)})",
                        other_ids,
                    )
            conn.execute(
                """UPDATE files SET edition_name=?,version_name=?,identity_confirmed=?,
                     version_preferred=? WHERE id=?""",
                (edition, version, int(confirmed), int(preferred), file_id),
            )
        return self.file(file_id)

    def siblings(self, file_id: int) -> list[dict[str, Any]]:
        current = self.file(file_id)
        if not current:
            return []
        with self.database.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT f.*,t.kind FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE f.title_id=? AND f.id<>?""",
                (current["title_id"], file_id),
            )]
        return [row for row in rows if same_playable_item(current, row)]
