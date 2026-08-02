from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .duplicates import DuplicateService


SEVERITIES = {"critical", "warning", "information"}
CATEGORIES = {"health", "identity", "completeness", "quality", "freshness", "storage"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _preferences(value: str | None) -> set[str]:
    return {
        item.strip().upper()
        for item in (value or "").split(",")
        if item.strip()
    }


def _is_hdr(value: str | None) -> bool:
    normalized = (value or "").strip().upper()
    return bool(normalized and normalized not in {"SDR", "UNKNOWN"})


class MediaIntelligenceEngine:
    """Explainable, read-only analysis over facts already stored in the catalog."""

    def __init__(self, database: Database):
        self.database = database
        self.duplicates = DuplicateService(database)

    def analyze(self) -> int:
        analyzed_at = _utc_now()
        candidates: list[dict[str, Any]] = []
        with self.database.connect() as conn:
            titles = {
                row["id"]: row for row in conn.execute(
                    """SELECT id,root_id,kind,title,year,metadata_title,
                              metadata_year,tvdb_id,tvdb_movie_id,tmdb_id,imdb_id
                       FROM titles"""
                )
            }
            files_by_title: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in conn.execute(
                """SELECT id,title_id,filename,path,season,episode_start,episode_end,
                          media_info_at,width,height,video_codec,audio_channels,bitrate,
                          container,dynamic_range
                   FROM files"""
            ):
                files_by_title[int(row["title_id"])].append(dict(row))

            for row in conn.execute(
                """SELECT f.id,f.title_id,f.filename,f.path,f.media_info_error,
                          t.root_id,t.title,t.metadata_title
                   FROM files f JOIN titles t ON t.id=f.title_id
                   WHERE COALESCE(f.media_info_error,'')!=''"""
            ):
                title_name = row["metadata_title"] or row["title"]
                plain_error = row["media_info_error"].partition(
                    "Technical details:"
                )[0].strip()
                candidates.append({
                    "fingerprint": f"media-unreadable:file:{row['id']}",
                    "rule_key": "media-unreadable",
                    "category": "health",
                    "severity": "critical",
                    "root_id": row["root_id"],
                    "title_id": row["title_id"],
                    "file_id": row["id"],
                    "summary": f"Media details could not be read for {row['filename']}",
                    "explanation": plain_error or (
                        "InfoMancer asked FFprobe to inspect this file, but the "
                        "container or media streams could not be read."
                    ),
                    "recommendation": (
                        "Confirm the storage location is available and try playing "
                        "the file. If it plays normally, reinspect it. If it does "
                        "not, restore or replace the file before attempting repairs."
                    ),
                    "evidence": {
                        "title": title_name,
                        "filename": row["filename"],
                        "path": row["path"],
                        "inspection_error": row["media_info_error"][:2000],
                    },
                })

            for title in titles.values():
                provider_ids = {
                    "TVDB series": title["tvdb_id"],
                    "TVDB movie": title["tvdb_movie_id"],
                    "TMDB": title["tmdb_id"],
                    "IMDb": title["imdb_id"],
                }
                providers = [label for label, value in provider_ids.items() if value]
                title_files = files_by_title.get(int(title["id"]), [])
                confidence = 0
                evidence_used: list[str] = []
                if providers:
                    confidence += 60
                    evidence_used.append("verified provider identifier")
                if title["metadata_title"]:
                    confidence += 15
                    evidence_used.append("provider title metadata")
                if title["metadata_year"] or title["year"]:
                    confidence += 10
                    evidence_used.append("release year")
                if title_files:
                    confidence += 15
                    evidence_used.append("cataloged file placement")
                if confidence >= 70:
                    continue
                name = title["metadata_title"] or title["title"]
                candidates.append({
                    "fingerprint": f"identity-confidence:title:{title['id']}",
                    "rule_key": "identity-confidence-low",
                    "category": "identity",
                    "severity": "warning",
                    "root_id": title["root_id"],
                    "title_id": title["id"],
                    "summary": f"{name} has low identity confidence ({confidence}/100)",
                    "explanation": (
                        "InfoMancer scored the independent catalog evidence for this "
                        "title. The available folder, year, provider, and file evidence "
                        "is not strong enough to treat the identity as confirmed."
                    ),
                    "recommendation": (
                        "Review suggested matches and select the correct movie or "
                        "series before relying on metadata or rename recommendations."
                    ),
                    "evidence": {
                        "confidence_score": f"{confidence}/100",
                        "evidence_used": evidence_used or ["catalog title only"],
                        "provider_identifiers": providers or ["none"],
                        "catalog_title": title["title"],
                        "catalog_year": title["year"],
                        "kind": title["kind"],
                    },
                })

            for title_id, title_files in files_by_title.items():
                title = titles.get(title_id)
                if not title or title["kind"] != "tv":
                    continue
                for file in title_files:
                    start = file.get("episode_start")
                    end = file.get("episode_end")
                    season = file.get("season")
                    if season is None or start is None or end is None or int(end) <= int(start):
                        continue
                    covered = [
                        f"S{int(season):02d}E{episode:02d}"
                        for episode in range(int(start), int(end) + 1)
                    ]
                    candidates.append({
                        "fingerprint": f"multi-episode:file:{file['id']}",
                        "rule_key": "multi-episode-file",
                        "category": "identity",
                        "severity": "information",
                        "root_id": title["root_id"],
                        "title_id": title_id,
                        "file_id": file["id"],
                        "summary": f"{file['filename']} contains multiple episode numbers",
                        "explanation": (
                            "The filename parser identified one file spanning a continuous "
                            "episode range. MIE uses that range when checking completeness "
                            "and duplicate coverage."
                        ),
                        "recommendation": (
                            "Confirm that the file really contains every listed episode. "
                            "If it does, no change is needed; otherwise correct the filename "
                            "and rescan the source."
                        ),
                        "evidence": {
                            "episode_range": covered,
                            "detected_from": "cataloged filename",
                            "path": file["path"],
                        },
                    })

            missing_by_title: dict[int, list[Any]] = defaultdict(list)
            for episode in conn.execute(
                """SELECT e.id,e.title_id,e.season,e.episode,e.name,e.aired
                   FROM expected_episodes e
                   WHERE e.season>0
                     AND (e.aired IS NULL OR e.aired<=date('now'))
                     AND NOT EXISTS (
                       SELECT 1 FROM files f
                       WHERE f.title_id=e.title_id AND f.season=e.season
                         AND e.episode BETWEEN f.episode_start
                                           AND COALESCE(f.episode_end,f.episode_start)
                     )
                   ORDER BY e.title_id,e.season,e.episode"""
            ):
                missing_by_title[episode["title_id"]].append(episode)
            for title_id, episodes in missing_by_title.items():
                title = titles.get(title_id)
                if not title:
                    continue
                name = title["metadata_title"] or title["title"]
                codes = [
                    f"S{row['season']:02d}E{row['episode']:02d}"
                    for row in episodes[:8]
                ]
                candidates.append({
                    "fingerprint": f"missing-episodes:title:{title_id}",
                    "rule_key": "missing-episodes",
                    "category": "completeness",
                    "severity": "warning",
                    "root_id": title["root_id"],
                    "title_id": title_id,
                    "expected_episode_id": episodes[0]["id"],
                    "summary": (
                        f"{name} is missing {len(episodes)} aired "
                        f"episode{'s' if len(episodes) != 1 else ''}"
                    ),
                    "explanation": (
                        "The matched episode order contains aired regular episodes "
                        "whose season and episode numbers were not found in local files."
                    ),
                    "recommendation": (
                        "Review the missing-episode list. If the files exist, check "
                        "their names or episode order and rescan the series."
                    ),
                    "evidence": {
                        "missing_count": len(episodes),
                        "sample_episode_codes": codes,
                    },
                })

            for root in conn.execute(
                """SELECT r.id,r.label,r.path,r.last_scanned_at,
                          SUM(CASE WHEN f.id IS NOT NULL
                                    AND f.media_info_at IS NULL
                                    AND COALESCE(f.media_info_error,'')=''
                                   THEN 1 ELSE 0 END) missing_details
                   FROM roots r
                   LEFT JOIN titles t ON t.root_id=r.id
                   LEFT JOIN files f ON f.title_id=t.id
                   WHERE r.enabled=1
                   GROUP BY r.id"""
            ):
                label = root["label"] or root["path"]
                missing_details = int(root["missing_details"] or 0)
                if missing_details:
                    candidates.append({
                        "fingerprint": f"technical-details:root:{root['id']}",
                        "rule_key": "technical-details-missing",
                        "category": "quality",
                        "severity": "information",
                        "root_id": root["id"],
                        "summary": (
                            f"{missing_details:,} files in {label} have not been inspected"
                        ),
                        "explanation": (
                            "Runtime, resolution, codecs, bitrate, container, and "
                            "HDR/SDR details have not yet been collected for these files."
                        ),
                        "recommendation": (
                            "Run media inspection for new or missing files when the "
                            "storage location is available."
                        ),
                        "evidence": {
                            "root": label,
                            "path": root["path"],
                            "file_count": missing_details,
                        },
                    })

                stale = conn.execute(
                    """SELECT CASE
                         WHEN ? IS NULL THEN 1
                         WHEN datetime(?) < datetime('now','-1 day') THEN 1
                         ELSE 0 END""",
                    (root["last_scanned_at"], root["last_scanned_at"]),
                ).fetchone()[0]
                if stale:
                    candidates.append({
                        "fingerprint": f"source-stale:root:{root['id']}",
                        "rule_key": "source-stale",
                        "category": "freshness",
                        "severity": "information",
                        "root_id": root["id"],
                        "summary": f"{label} needs a fresh scan",
                        "explanation": (
                            "This source has never been scanned or its most recent "
                            "scan is more than one day old."
                        ),
                        "recommendation": (
                            "Scan the source to discover new media and update files "
                            "that were moved or removed."
                        ),
                        "evidence": {
                            "root": label,
                            "path": root["path"],
                            "last_scanned_at": root["last_scanned_at"],
                        },
                    })

            profiles = {
                int(row["root_id"]): dict(row)
                for row in conn.execute("SELECT * FROM mie_quality_profiles")
            }
            title_quality_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for title_id, title_files in files_by_title.items():
                title = titles.get(title_id)
                if not title or int(title["root_id"]) not in profiles:
                    continue
                profile = profiles[int(title["root_id"])]
                preferred_codecs = _preferences(profile["preferred_video_codecs"])
                preferred_containers = _preferences(profile["preferred_containers"])
                for file in title_files:
                    if not file.get("media_info_at"):
                        continue
                    title_quality_rows[title_id].append(file)
                    violations: list[str] = []
                    width = int(file.get("width") or 0)
                    height = int(file.get("height") or 0)
                    bitrate = int(file.get("bitrate") or 0)
                    channels = int(file.get("audio_channels") or 0)
                    if profile["minimum_width"] and width < int(profile["minimum_width"]):
                        violations.append(
                            f"width {width or 'unknown'} is below {profile['minimum_width']} pixels"
                        )
                    if profile["minimum_height"] and height < int(profile["minimum_height"]):
                        violations.append(
                            f"height {height or 'unknown'} is below {profile['minimum_height']} pixels"
                        )
                    if profile["minimum_bitrate"] and bitrate < int(profile["minimum_bitrate"]):
                        violations.append(
                            f"bitrate {bitrate or 'unknown'} is below {profile['minimum_bitrate']} bps"
                        )
                    codec = str(file.get("video_codec") or "").upper()
                    if preferred_codecs and codec not in preferred_codecs:
                        violations.append(
                            f"video codec {codec or 'unknown'} is not one of "
                            f"{', '.join(sorted(preferred_codecs))}"
                        )
                    container = str(file.get("container") or "").upper()
                    if preferred_containers and container not in preferred_containers:
                        violations.append(
                            f"container {container or 'unknown'} is not one of "
                            f"{', '.join(sorted(preferred_containers))}"
                        )
                    if profile["minimum_audio_channels"] and channels < int(
                        profile["minimum_audio_channels"]
                    ):
                        violations.append(
                            f"audio channels {channels or 'unknown'} are below "
                            f"{profile['minimum_audio_channels']}"
                        )
                    expected_range = profile["dynamic_range"]
                    if expected_range == "hdr" and not _is_hdr(file.get("dynamic_range")):
                        violations.append("dynamic range is not HDR")
                    if expected_range == "sdr" and _is_hdr(file.get("dynamic_range")):
                        violations.append("dynamic range is HDR instead of the preferred SDR")
                    if violations:
                        title_name = title["metadata_title"] or title["title"]
                        candidates.append({
                            "fingerprint": f"quality-preference:file:{file['id']}",
                            "rule_key": "quality-preference",
                            "category": "quality",
                            "severity": "warning",
                            "root_id": title["root_id"],
                            "title_id": title_id,
                            "file_id": file["id"],
                            "summary": f"{file['filename']} is outside this source's quality profile",
                            "explanation": (
                                f"{title_name} was compared with the preferences a Librarian "
                                "set for this source. " + "; ".join(violations) + "."
                            ),
                            "recommendation": (
                                "Review whether this is an intentional edition or an older copy. "
                                "Keep it when appropriate, replace it manually if desired, or "
                                "adjust the source profile. MIE will not change the file."
                            ),
                            "evidence": {
                                "profile_checks_failed": violations,
                                "resolution": f"{width}x{height}" if width and height else "unknown",
                                "video_codec": codec or "unknown",
                                "container": container or "unknown",
                                "bitrate_bps": bitrate or None,
                                "audio_channels": channels or None,
                                "dynamic_range": file.get("dynamic_range") or "unknown",
                            },
                        })

            for title_id, inspected_files in title_quality_rows.items():
                title = titles[title_id]
                profile = profiles[int(title["root_id"])]
                if not profile["detect_outliers"] or len(inspected_files) < 3:
                    continue
                signatures = [(
                    int(file.get("width") or 0), int(file.get("height") or 0),
                    str(file.get("video_codec") or "").upper(),
                    str(file.get("container") or "").upper(),
                    str(file.get("dynamic_range") or "").upper(),
                    int(file.get("audio_channels") or 0),
                ) for file in inspected_files]
                dominant, dominant_count = Counter(signatures).most_common(1)[0]
                if dominant_count < 2 or dominant_count <= len(signatures) / 2:
                    continue
                for file, signature in zip(inspected_files, signatures):
                    if signature == dominant:
                        continue
                    candidates.append({
                        "fingerprint": f"quality-consistency:file:{file['id']}",
                        "rule_key": "quality-consistency",
                        "category": "quality",
                        "severity": "information",
                        "root_id": title["root_id"],
                        "title_id": title_id,
                        "file_id": file["id"],
                        "summary": f"{file['filename']} differs from this title's usual profile",
                        "explanation": (
                            f"{dominant_count} of {len(inspected_files)} inspected files for "
                            "this title share one technical profile, while this file differs."
                        ),
                        "recommendation": (
                            "Review the difference as a possible special edition or quality "
                            "outlier. No change is needed when the variation is intentional."
                        ),
                        "evidence": {
                            "file_profile": list(signature),
                            "dominant_profile": list(dominant),
                            "dominant_file_count": dominant_count,
                            "inspected_file_count": len(inspected_files),
                        },
                    })

            duplicate_candidates = self.duplicates.candidates()
            for duplicate in duplicate_candidates:
                file_a = duplicate["file_a"]
                file_b = duplicate["file_b"]
                candidates.append({
                    "fingerprint": f"duplicate-recovery:{duplicate['pair']}",
                    "rule_key": "duplicate-storage-recovery",
                    "category": "storage",
                    "severity": (
                        "warning" if duplicate["classification"] == "verified_exact"
                        else "information"
                    ),
                    "title_id": duplicate["title_id"],
                    "file_id": duplicate["preferred_id"] or file_a["id"],
                    "summary": f"Review storage recovery options for {duplicate['title_name']}",
                    "explanation": duplicate["explanation"],
                    "recommendation": duplicate["recommendation"],
                    "evidence": {
                        "classification": duplicate["label"],
                        "recommended_keep": duplicate["recommended_keep"],
                        "file_a": file_a["filename"],
                        "file_b": file_b["filename"],
                        "source_a": file_a["root_label"],
                        "source_b": file_b["root_label"],
                        "recovery_evidence": duplicate["recovery_reasons"],
                        "safe_to_remove_automatically": "No",
                    },
                })
            conn.execute(
                """UPDATE mie_findings
                   SET status='resolved',resolved_at=?
                   WHERE status IN ('active','dismissed')""",
                (analyzed_at,),
            )
            for finding in candidates:
                conn.execute(
                    """INSERT INTO mie_findings(
                         fingerprint,rule_key,category,severity,root_id,title_id,
                         file_id,expected_episode_id,summary,explanation,
                         recommendation,evidence_json,status,first_seen_at,last_seen_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'active',?,?)
                       ON CONFLICT(fingerprint) DO UPDATE SET
                         rule_key=excluded.rule_key,
                         category=excluded.category,
                         severity=excluded.severity,
                         root_id=excluded.root_id,
                         title_id=excluded.title_id,
                         file_id=excluded.file_id,
                         expected_episode_id=excluded.expected_episode_id,
                         summary=excluded.summary,
                         explanation=excluded.explanation,
                         recommendation=excluded.recommendation,
                         evidence_json=excluded.evidence_json,
                         status=CASE WHEN mie_findings.dismissed_at IS NOT NULL
                                     THEN 'dismissed' ELSE 'active' END,
                         last_seen_at=excluded.last_seen_at,
                         resolved_at=NULL""",
                    (
                        finding["fingerprint"], finding["rule_key"],
                        finding["category"], finding["severity"],
                        finding.get("root_id"), finding.get("title_id"),
                        finding.get("file_id"),
                        finding.get("expected_episode_id"),
                        finding["summary"], finding["explanation"],
                        finding["recommendation"], _json(finding["evidence"]),
                        analyzed_at, analyzed_at,
                    ),
                )
            conn.execute(
                """INSERT INTO mie_analysis_state(id,last_analyzed_at,finding_count)
                   VALUES (1,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     last_analyzed_at=excluded.last_analyzed_at,
                     finding_count=excluded.finding_count""",
                (analyzed_at, len(candidates)),
            )
        return len(candidates)

    def quality_profiles(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT r.id root_id,r.label,r.path,r.kind,
                          p.minimum_width,p.minimum_height,p.minimum_bitrate,
                          p.preferred_video_codecs,p.preferred_containers,
                          p.minimum_audio_channels,p.dynamic_range,p.detect_outliers,
                          p.updated_at
                   FROM roots r LEFT JOIN mie_quality_profiles p ON p.root_id=r.id
                   WHERE r.enabled=1 ORDER BY r.kind,r.label COLLATE NOCASE,r.path"""
            ).fetchall()
        profiles = []
        for row in rows:
            profile = dict(row)
            profile["configured"] = row["updated_at"] is not None
            profile["preferred_video_codecs"] = row["preferred_video_codecs"] or ""
            profile["preferred_containers"] = row["preferred_containers"] or ""
            profile["dynamic_range"] = row["dynamic_range"] or "any"
            profile["detect_outliers"] = (
                True if row["detect_outliers"] is None else bool(row["detect_outliers"])
            )
            profile["minimum_bitrate_mbps"] = (
                round(int(row["minimum_bitrate"]) / 1_000_000, 2)
                if row["minimum_bitrate"] else ""
            )
            profiles.append(profile)
        return profiles

    def save_quality_profile(
        self, root_id: int, *, minimum_width: str = "", minimum_height: str = "",
        minimum_bitrate_mbps: str = "", preferred_video_codecs: str = "",
        preferred_containers: str = "", minimum_audio_channels: str = "",
        dynamic_range: str = "any", detect_outliers: bool = True,
        user_id: int | None = None,
    ) -> None:
        def optional_integer(value: str, label: str, maximum: int) -> int | None:
            value = value.strip()
            if not value:
                return None
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ValueError(f"{label} must be a whole number or left blank.") from exc
            if parsed < 1 or parsed > maximum:
                raise ValueError(f"{label} must be between 1 and {maximum:,}.")
            return parsed

        width = optional_integer(minimum_width, "Minimum width", 16_384)
        height = optional_integer(minimum_height, "Minimum height", 16_384)
        channels = optional_integer(minimum_audio_channels, "Minimum audio channels", 32)
        bitrate_text = minimum_bitrate_mbps.strip()
        bitrate = None
        if bitrate_text:
            try:
                bitrate_mbps = float(bitrate_text)
            except ValueError as exc:
                raise ValueError(
                    "Minimum bitrate must be a number in Mbps or left blank."
                ) from exc
            if bitrate_mbps <= 0 or bitrate_mbps > 1_000:
                raise ValueError("Minimum bitrate must be greater than 0 and at most 1,000 Mbps.")
            bitrate = round(bitrate_mbps * 1_000_000)

        def normalized_list(value: str, label: str) -> str:
            items = []
            for item in value.split(","):
                normalized = item.strip().upper()
                if not normalized:
                    continue
                if not re.fullmatch(r"[A-Z0-9._+\-]{1,30}", normalized):
                    raise ValueError(
                        f"{label} entries may use letters, numbers, dots, plus signs, "
                        "dashes, or underscores. Separate multiple entries with commas."
                    )
                if normalized not in items:
                    items.append(normalized)
            return ", ".join(items)

        codecs = normalized_list(preferred_video_codecs, "Preferred video codec")
        containers = normalized_list(preferred_containers, "Preferred container")
        dynamic_range = dynamic_range.strip().casefold()
        if dynamic_range not in {"any", "sdr", "hdr"}:
            raise ValueError("Dynamic range preference must be Any, SDR, or HDR.")
        with self.database.connect() as conn:
            if not conn.execute(
                "SELECT 1 FROM roots WHERE id=? AND enabled=1", (root_id,)
            ).fetchone():
                raise ValueError(
                    "That media source is no longer available. Refresh Library Health and try again."
                )
            conn.execute(
                """INSERT INTO mie_quality_profiles(
                     root_id,minimum_width,minimum_height,minimum_bitrate,
                     preferred_video_codecs,preferred_containers,
                     minimum_audio_channels,dynamic_range,detect_outliers,
                     updated_by,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(root_id) DO UPDATE SET
                     minimum_width=excluded.minimum_width,
                     minimum_height=excluded.minimum_height,
                     minimum_bitrate=excluded.minimum_bitrate,
                     preferred_video_codecs=excluded.preferred_video_codecs,
                     preferred_containers=excluded.preferred_containers,
                     minimum_audio_channels=excluded.minimum_audio_channels,
                     dynamic_range=excluded.dynamic_range,
                     detect_outliers=excluded.detect_outliers,
                     updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                (
                    root_id, width, height, bitrate, codecs, containers, channels,
                    dynamic_range, int(detect_outliers), user_id,
                ),
            )

    def delete_quality_profile(self, root_id: int) -> bool:
        with self.database.connect() as conn:
            result = conn.execute(
                "DELETE FROM mie_quality_profiles WHERE root_id=?", (root_id,)
            )
        return result.rowcount == 1

    def summary(self) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT
                     SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active,
                     SUM(CASE WHEN status='active' AND severity='critical'
                              THEN 1 ELSE 0 END) critical,
                     SUM(CASE WHEN status='active' AND severity='warning'
                              THEN 1 ELSE 0 END) warning,
                     SUM(CASE WHEN status='dismissed' THEN 1 ELSE 0 END) dismissed
                   FROM mie_findings"""
            ).fetchone()
            state = conn.execute(
                "SELECT last_analyzed_at FROM mie_analysis_state WHERE id=1"
            ).fetchone()
        return {
            "active": int(row["active"] or 0),
            "critical": int(row["critical"] or 0),
            "warning": int(row["warning"] or 0),
            "dismissed": int(row["dismissed"] or 0),
            "last_analyzed_at": state["last_analyzed_at"] if state else None,
        }

    def findings(
        self, *, status: str = "active", severity: str = "",
        category: str = "",
    ) -> list[dict[str, Any]]:
        status = status if status in {"active", "dismissed", "resolved"} else "active"
        conditions = ["mf.status=?"]
        params: list[Any] = [status]
        if severity in SEVERITIES:
            conditions.append("mf.severity=?")
            params.append(severity)
        if category in CATEGORIES:
            conditions.append("mf.category=?")
            params.append(category)
        with self.database.connect() as conn:
            rows = conn.execute(
                f"""SELECT mf.*,COALESCE(t.metadata_title,t.title) title_name,
                           t.kind title_kind,f.filename,r.label root_label,r.path root_path
                    FROM mie_findings mf
                    LEFT JOIN titles t ON t.id=mf.title_id
                    LEFT JOIN files f ON f.id=mf.file_id
                    LEFT JOIN roots r ON r.id=mf.root_id
                    WHERE {' AND '.join(conditions)}
                    ORDER BY
                      CASE mf.severity WHEN 'critical' THEN 1
                           WHEN 'warning' THEN 2 ELSE 3 END,
                      mf.category,mf.last_seen_at DESC,mf.id DESC""",
                params,
            ).fetchall()
        findings = []
        for row in rows:
            finding = dict(row)
            try:
                finding["evidence"] = json.loads(finding["evidence_json"] or "{}")
            except (TypeError, ValueError):
                finding["evidence"] = {}
            finding["href"] = self.finding_href(finding)
            findings.append(finding)
        return findings

    @staticmethod
    def finding_href(finding: dict[str, Any]) -> str:
        if finding["rule_key"] == "missing-episodes" and finding.get("title_id"):
            return (
                f"/titles/{finding['title_id']}?show_missing=1#missing-panel"
            )
        if finding["rule_key"] in {"unmatched-title", "identity-confidence-low"} \
                and finding.get("title_id"):
            return f"/titles/{finding['title_id']}/tvdb"
        if finding["rule_key"] in {
            "duplicate-candidates", "duplicate-storage-recovery",
        }:
            return "/duplicates"
        if finding.get("file_id") and finding.get("title_id"):
            return f"/titles/{finding['title_id']}"
        if finding["rule_key"] == "technical-details-missing":
            return "/settings/system#media-information"
        if finding.get("root_id"):
            return "/sources"
        if finding.get("title_id"):
            return f"/titles/{finding['title_id']}"
        return "/library"

    def dismiss(self, finding_id: int, user_id: int | None) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE mie_findings
                   SET status='dismissed',dismissed_at=CURRENT_TIMESTAMP,
                       dismissed_by=?
                   WHERE id=? AND status='active'""",
                (user_id if user_id and user_id > 0 else None, finding_id),
            )
        return bool(cursor.rowcount)

    def restore(self, finding_id: int) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """UPDATE mie_findings
                   SET status='active',dismissed_at=NULL,dismissed_by=NULL
                   WHERE id=? AND status='dismissed'""",
                (finding_id,),
            )
        return bool(cursor.rowcount)
