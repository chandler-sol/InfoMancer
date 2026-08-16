from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .context import RouteContext


def _format_bytes(value: int | None) -> str:
    size = float(value or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit in {"B", "KB"}:
        return f"{size:.0f} {unit}"
    return f"{size:.1f} {unit}"


def _season_label(season: int | None) -> str:
    if season is None:
        return "Other files"
    if season == 0:
        return "Specials"
    return f"Season {season:02d}"


def _season_key(season: int | None) -> str:
    return "other" if season is None else str(season)


def _parse_season_key(value: str) -> int | None:
    if value == "other":
        return None
    try:
        season = int(value)
    except ValueError as exc:
        raise HTTPException(404, "Season not found") from exc
    if season < 0 or season > 999:
        raise HTTPException(404, "Season not found")
    return season


def _episode_code(row, season: int | None) -> str:
    start = row["episode_start"]
    if season is None or start is None:
        return "File"
    prefix = f"S{season:02d}E{int(start):02d}"
    end = row["episode_end"]
    if end is not None and int(end) > int(start):
        prefix += f"-E{int(end):02d}"
    return prefix


def build_router(ctx: RouteContext):
    router = APIRouter()
    db = ctx.live("db")

    def _tv_title(title_id: int):
        with db.connect() as conn:
            title = conn.execute(
                "SELECT id,kind FROM titles WHERE id=?", (title_id,)
            ).fetchone()
        if title is None or title["kind"] != "tv":
            raise HTTPException(404, "TV title not found")
        return title

    @router.get("/api/titles/{title_id}/inspector-media")
    def inspector_media_seasons(title_id: int):
        _tv_title(title_id)
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT season, COUNT(*) AS file_count,
                       COALESCE(SUM(size_bytes), 0) AS total_size
                FROM files
                WHERE title_id=?
                GROUP BY season
                ORDER BY
                  CASE WHEN season = 0 THEN 0 WHEN season IS NULL THEN 2 ELSE 1 END,
                  season
                """,
                (title_id,),
            ).fetchall()
        seasons = [
            {
                "key": _season_key(row["season"]),
                "season": row["season"],
                "label": _season_label(row["season"]),
                "file_count": int(row["file_count"] or 0),
                "total_size": int(row["total_size"] or 0),
                "total_size_display": _format_bytes(row["total_size"]),
            }
            for row in rows
        ]
        return {"title_id": title_id, "seasons": seasons}

    @router.get("/api/titles/{title_id}/inspector-media/{season_key}")
    def inspector_media_season(title_id: int, season_key: str):
        _tv_title(title_id)
        season = _parse_season_key(season_key)
        clause = "season IS NULL" if season is None else "season=?"
        params = (title_id,) if season is None else (title_id, season)
        with db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, filename, season, episode_start, episode_end, episode_name,
                       size_bytes, runtime_seconds, width, height, video_codec,
                       audio_codec, audio_channels, dynamic_range, container
                FROM files
                WHERE title_id=? AND {clause}
                ORDER BY
                  CASE WHEN episode_start IS NULL THEN 1 ELSE 0 END,
                  episode_start, episode_end, filename COLLATE NOCASE, id
                """,
                params,
            ).fetchall()
        files = []
        for row in rows:
            resolution = (
                f"{row['width']}×{row['height']}"
                if row["width"] and row["height"] else ""
            )
            duration = int(row["runtime_seconds"] or 0)
            runtime = ""
            if duration:
                minutes = max(1, round(duration / 60))
                runtime = f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes} min"
            files.append({
                "id": int(row["id"]),
                "filename": row["filename"],
                "episode_name": row["episode_name"] or "",
                "episode_code": _episode_code(row, season),
                "size_display": _format_bytes(row["size_bytes"]),
                "runtime_display": runtime,
                "resolution_display": resolution,
                "video_codec": row["video_codec"] or "",
                "audio_codec": row["audio_codec"] or "",
                "audio_channels": row["audio_channels"],
                "dynamic_range": row["dynamic_range"] or "",
                "container": row["container"] or "",
            })
        return {
            "title_id": title_id,
            "season": season,
            "key": season_key,
            "label": _season_label(season),
            "files": files,
        }

    return router, {}
