from __future__ import annotations

import json
import sqlite3
from typing import Any


FILTER_KEYS = {
    "genre", "year_from", "year_to", "resolution", "quality", "root_id",
    "favorite", "missing_episodes", "health_category",
}


def normalize_filters(values: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in FILTER_KEYS:
        value = str(values.get(key) or "").strip()
        if not value:
            continue
        if key in {"year_from", "year_to"}:
            if not value.isdigit() or not 1800 <= int(value) <= 2200:
                raise ValueError("Years must be between 1800 and 2200.")
        elif key == "resolution" and value not in {"720", "1080", "2160"}:
            raise ValueError("Resolution must be 720p, 1080p, or 4K.")
        elif key == "quality" and value not in {"hdr", "sdr", "high-bitrate"}:
            raise ValueError("Quality must be HDR, SDR, or high bitrate.")
        elif key in {"favorite", "missing_episodes"} and value not in {"yes", "no"}:
            raise ValueError("Favorite and missing-episode filters must be Yes or No.")
        elif key == "root_id" and not value.isdigit():
            raise ValueError("Choose a valid source.")
        result[key] = value[:100]
    if result.get("year_from") and result.get("year_to"):
        if int(result["year_from"]) > int(result["year_to"]):
            raise ValueError("The starting year must not be after the ending year.")
    if not result:
        raise ValueError("Choose at least one filter before previewing or saving.")
    return result


def encode_filters(filters: dict[str, str]) -> str:
    return json.dumps(filters, sort_keys=True, separators=(",", ":"))


def decode_filters(value: str) -> dict[str, str]:
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return {key: str(item) for key, item in data.items() if key in FILTER_KEYS}


def matching_titles(conn: sqlite3.Connection, filters: dict[str, str], user_id: int) -> list[dict]:
    conditions: list[str] = []
    params: list[Any] = [user_id]
    if filters.get("genre"):
        conditions.append("INSTR(','||LOWER(COALESCE(t.genres,''))||',',?)>0")
        params.append(f",{filters['genre'].lower()},")
    if filters.get("year_from"):
        conditions.append("COALESCE(t.metadata_year,t.year)>=?")
        params.append(int(filters["year_from"]))
    if filters.get("year_to"):
        conditions.append("COALESCE(t.metadata_year,t.year)<=?")
        params.append(int(filters["year_to"]))
    if filters.get("root_id"):
        conditions.append("t.root_id=?")
        params.append(int(filters["root_id"]))
    if filters.get("favorite"):
        conditions.append("COALESCE(uts.favorite,0)=?")
        params.append(1 if filters["favorite"] == "yes" else 0)
    if filters.get("resolution"):
        minimum = {"720": 1280, "1080": 1920, "2160": 3840}[filters["resolution"]]
        conditions.append("EXISTS(SELECT 1 FROM files rf WHERE rf.title_id=t.id AND rf.width>=?)")
        params.append(minimum)
    if filters.get("quality") == "hdr":
        conditions.append("EXISTS(SELECT 1 FROM files qf WHERE qf.title_id=t.id AND LOWER(COALESCE(qf.dynamic_range,'')) LIKE 'hdr%')")
    elif filters.get("quality") == "sdr":
        conditions.append("EXISTS(SELECT 1 FROM files qf WHERE qf.title_id=t.id AND LOWER(COALESCE(qf.dynamic_range,''))='sdr')")
    elif filters.get("quality") == "high-bitrate":
        conditions.append("EXISTS(SELECT 1 FROM files qf WHERE qf.title_id=t.id AND qf.bitrate>=15000000)")
    if filters.get("missing_episodes"):
        missing = """EXISTS(SELECT 1 FROM expected_episodes ee WHERE ee.title_id=t.id AND ee.season>0
          AND NOT EXISTS(SELECT 1 FROM files mf WHERE mf.title_id=t.id AND mf.season=ee.season
            AND ee.episode BETWEEN mf.episode_start AND COALESCE(mf.episode_end,mf.episode_start)))"""
        conditions.append(missing if filters["missing_episodes"] == "yes" else f"NOT ({missing})")
    if filters.get("health_category"):
        conditions.append("EXISTS(SELECT 1 FROM mie_findings sf WHERE sf.title_id=t.id AND sf.status='active' AND sf.category=?)")
        params.append(filters["health_category"])
    where = " AND ".join(conditions) if conditions else "1"
    rows = conn.execute(
        f"""SELECT t.*,COALESCE(NULLIF(t.metadata_title,''),t.title) display_title,
                   COALESCE(t.metadata_year,t.year) display_year,COALESCE(uts.favorite,0) favorite
            FROM titles t LEFT JOIN user_title_state uts ON uts.title_id=t.id AND uts.user_id=?
            WHERE {where} ORDER BY display_title COLLATE NOCASE""", params,
    ).fetchall()
    return [dict(row) for row in rows]
