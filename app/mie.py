from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database
from .duplicates import DuplicateService


SEVERITIES = {"critical", "warning", "information"}
CATEGORIES = {"health", "identity", "completeness", "quality", "freshness", "storage"}
DEFAULT_CALIBRATION = {
    "identity_warning_threshold": 70,
    "source_stale_hours": 24,
    "critical_weight": 20,
    "warning_weight": 8,
    "information_weight": 2,
}
FEEDBACK_REASONS = {"expected", "incorrect", "resolved_elsewhere", "other"}
FEEDBACK_SCOPES = {"finding", "title", "source"}


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


def _identity_evidence(title: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    providers = {
        "TVDB series": title.get("tvdb_id"), "TVDB movie": title.get("tvdb_movie_id"),
        "TMDB": title.get("tmdb_id"), "IMDb": title.get("imdb_id"),
    }
    provider_labels = [label for label, value in providers.items() if value]
    catalog_title = (title.get("title") or "").strip()
    metadata_title = (title.get("metadata_title") or "").strip()
    catalog_year = title.get("year")
    metadata_year = title.get("metadata_year")
    title_conflict = bool(metadata_title and catalog_title and metadata_title.casefold() != catalog_title.casefold())
    year_conflict = bool(metadata_year and catalog_year and int(metadata_year) != int(catalog_year))
    inspected = [item for item in files if item.get("runtime_seconds")]
    breakdown = [
        {"key": "provider", "label": "Provider identity", "score": 60 if provider_labels else 0, "maximum": 60,
         "detail": ", ".join(provider_labels) if provider_labels else "No provider identifier saved"},
        {"key": "title", "label": "Title metadata", "score": 15 if metadata_title else 0, "maximum": 15,
         "detail": metadata_title or "Catalog title only", "conflict": title_conflict},
        {"key": "year", "label": "Release year", "score": 10 if metadata_year or catalog_year else 0, "maximum": 10,
         "detail": str(metadata_year or catalog_year or "Unknown"), "conflict": year_conflict},
        {"key": "folder", "label": "Catalog placement", "score": 15 if files else 0, "maximum": 15,
         "detail": f"{len(files):,} cataloged file{'s' if len(files) != 1 else ''}" if files else "No files cataloged"},
    ]
    conflicts = []
    if title_conflict:
        conflicts.append(f"Folder/catalog title is '{catalog_title}', while provider metadata says '{metadata_title}'.")
    if year_conflict:
        conflicts.append(f"Catalog year is {catalog_year}, while provider metadata says {metadata_year}.")
    recommendations = []
    if not provider_labels:
        recommendations.append("Review a provider match before relying on metadata or rename suggestions.")
    if conflicts:
        recommendations.append("Confirm the intended edition before changing names; InfoMancer will not apply a correction automatically.")
    if not recommendations:
        recommendations.append("The saved identity evidence is consistent. No identity change is recommended.")
    runtimes = [float(item["runtime_seconds"]) for item in inspected]
    return {
        "score": sum(item["score"] for item in breakdown), "breakdown": breakdown,
        "providers": providers, "conflicts": conflicts, "recommendations": recommendations,
        "runtime": {"inspected": len(inspected), "minimum": min(runtimes) if runtimes else None,
                    "maximum": max(runtimes) if runtimes else None},
    }


class MediaIntelligenceEngine:
    """Explainable, read-only analysis over facts already stored in the catalog."""

    def __init__(self, database: Database):
        self.database = database
        self.duplicates = DuplicateService(database)

    def analyze(self) -> int:
        analyzed_at = _utc_now()
        candidates: list[dict[str, Any]] = []
        with self.database.connect() as conn:
            calibration_row = conn.execute(
                "SELECT * FROM mie_calibration WHERE id=1"
            ).fetchone()
            calibration = dict(DEFAULT_CALIBRATION)
            if calibration_row:
                calibration.update({key: calibration_row[key] for key in DEFAULT_CALIBRATION})
            titles = {
                row["id"]: row for row in conn.execute(
                    """SELECT id,root_id,kind,title,year,metadata_title,
                              metadata_year,tvdb_id,tvdb_movie_id,tmdb_id,imdb_id,
                              poster_url,metadata_refreshed_at,
                              (SELECT COUNT(*) FROM title_credits tc WHERE tc.title_id=titles.id) credit_count,
                              (SELECT COUNT(*) FROM expected_episodes ee WHERE ee.title_id=titles.id) episode_count
                       FROM titles"""
                )
            }
            files_by_title: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in conn.execute(
                """SELECT id,title_id,filename,path,season,episode_start,episode_end,runtime_seconds,
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
                title_files = files_by_title.get(int(title["id"]), [])
                identity = _identity_evidence(dict(title), title_files)
                confidence = identity["score"]
                if confidence >= calibration["identity_warning_threshold"]:
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
                        "evidence_used": [item["label"] for item in identity["breakdown"] if item["score"]] or ["catalog title only"],
                        "provider_identifiers": [label for label, value in identity["providers"].items() if value] or ["none"],
                        "catalog_title": title["title"],
                        "catalog_year": title["year"],
                        "kind": title["kind"],
                    },
                })

            for title in titles.values():
                matched = any((title["tvdb_id"], title["tvdb_movie_id"], title["tmdb_id"], title["imdb_id"]))
                if not matched:
                    name = title["metadata_title"] or title["title"]
                    candidates.append({
                        "fingerprint": f"metadata-identifiers:title:{title['id']}",
                        "rule_key": "metadata-identifiers-missing", "category": "identity",
                        "severity": "warning", "root_id": title["root_id"], "title_id": title["id"],
                        "summary": f"{name} has no provider identifier",
                        "explanation": "The catalog title is not linked to TVDB, TMDB, or IMDb metadata.",
                        "recommendation": "Review and confirm a provider match before refreshing metadata.",
                        "evidence": {"provider_identifiers": []},
                    })
                    continue
                name = title["metadata_title"] or title["title"]
                common = {"root_id": title["root_id"], "title_id": title["id"]}
                if not title["poster_url"]:
                    candidates.append({
                        "fingerprint": f"metadata-artwork:title:{title['id']}",
                        "rule_key": "metadata-artwork-missing", "category": "completeness",
                        "severity": "information", **common,
                        "summary": f"{name} has no artwork",
                        "explanation": "The title is matched, but no poster artwork is stored in the catalog.",
                        "recommendation": "Refresh this title's metadata or choose artwork from its title page.",
                        "evidence": {"provider_ids_present": True},
                    })
                if not title["credit_count"]:
                    candidates.append({
                        "fingerprint": f"metadata-credits:title:{title['id']}",
                        "rule_key": "metadata-credits-missing", "category": "completeness",
                        "severity": "information", **common,
                        "summary": f"{name} has no stored credits",
                        "explanation": "No cast, director, or writer credits are stored for this matched title.",
                        "recommendation": "Queue an incremental metadata refresh for this title.",
                        "evidence": {"credit_count": 0},
                    })
                if title["kind"] == "tv" and not title["episode_count"]:
                    candidates.append({
                        "fingerprint": f"metadata-episodes:title:{title['id']}",
                        "rule_key": "metadata-episodes-incomplete", "category": "completeness",
                        "severity": "warning", **common,
                        "summary": f"{name} has no provider episode data",
                        "explanation": "The series is matched, but its expected episode list is empty.",
                        "recommendation": "Refresh the series match and episode metadata before checking for gaps.",
                        "evidence": {"expected_episode_count": 0},
                    })
                refreshed = str(title["metadata_refreshed_at"] or "")
                try:
                    refreshed_at = datetime.fromisoformat(refreshed.replace("Z", "+00:00"))
                    if refreshed_at.tzinfo is None:
                        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
                    is_stale = refreshed_at < datetime.now(timezone.utc) - timedelta(days=30)
                except ValueError:
                    is_stale = bool(refreshed)
                if is_stale:
                    candidates.append({
                        "fingerprint": f"metadata-stale:title:{title['id']}",
                        "rule_key": "metadata-stale", "category": "freshness",
                        "severity": "information", **common,
                        "summary": f"{name} metadata may be stale",
                        "explanation": "The saved provider metadata has not been refreshed recently.",
                        "recommendation": "Queue an incremental refresh; existing metadata remains available if it fails.",
                        "evidence": {"last_refreshed_at": refreshed},
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
                """SELECT r.id,r.label,r.path,r.last_scanned_at,r.health_status,
                           r.last_checked_at,r.last_seen_at,r.last_error,
                           r.last_file_count,r.last_observed_file_count,
                           r.guard_preserved_count,
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
                source_status = root["health_status"] or "unknown"
                if source_status == "offline":
                    candidates.append({
                        "fingerprint": f"source-offline:root:{root['id']}",
                        "rule_key": "source-offline", "category": "health",
                        "severity": "critical", "root_id": root["id"],
                        "summary": f"{label} is unavailable",
                        "explanation": (
                            "Source Guard could not open the configured media folder. "
                            "The existing catalog was preserved and no missing-file cleanup ran."
                        ),
                        "recommendation": (
                            "Check the NAS, mount, drive mapping, and service permissions, "
                            "then run Check connection before scanning again."
                        ),
                        "evidence": {
                            "root": label, "path": root["path"],
                            "last_checked_at": root["last_checked_at"],
                            "last_seen_at": root["last_seen_at"],
                            "protected_catalog_files": root["last_file_count"],
                            "connection_error": root["last_error"],
                        },
                    })
                elif source_status == "degraded":
                    candidates.append({
                        "fingerprint": f"source-degraded:root:{root['id']}",
                        "rule_key": "source-degraded", "category": "health",
                        "severity": "warning", "root_id": root["id"],
                        "summary": f"{label} returned an incomplete view",
                        "explanation": (
                            "Source Guard detected read errors, an unexpectedly empty mount, "
                            "or a sharp file-count drop. Catalog cleanup was blocked."
                        ),
                        "recommendation": (
                            "Restore the storage connection and scan again. If the change was "
                            "intentional, preview and explicitly confirm catalog reconciliation."
                        ),
                        "evidence": {
                            "root": label, "path": root["path"],
                            "last_known_files": root["last_file_count"],
                            "observed_files": root["last_observed_file_count"],
                            "protected_catalog_files": root["guard_preserved_count"],
                            "last_checked_at": root["last_checked_at"],
                            "details": root["last_error"],
                        },
                    })
                missing_details = int(root["missing_details"] or 0)
                if missing_details and source_status != "offline":
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
                         WHEN datetime(?) < datetime('now',?) THEN 1
                         ELSE 0 END""",
                    (
                        root["last_scanned_at"], root["last_scanned_at"],
                        f"-{calibration['source_stale_hours']} hours",
                    ),
                ).fetchone()[0]
                if stale and source_status not in {"offline", "degraded"}:
                    candidates.append({
                        "fingerprint": f"source-stale:root:{root['id']}",
                        "rule_key": "source-stale",
                        "category": "freshness",
                        "severity": "information",
                        "root_id": root["id"],
                        "summary": f"{label} needs a fresh scan",
                        "explanation": (
                            "This source has never been scanned or its most recent "
                            f"scan is more than {calibration['source_stale_hours']} hours old."
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
            feedback_rows = conn.execute(
                """SELECT * FROM mie_feedback
                   WHERE active=1 AND reason IN ('expected','incorrect')"""
            ).fetchall()

            def is_suppressed(finding: dict[str, Any]) -> bool:
                for feedback in feedback_rows:
                    if feedback["rule_key"] != finding["rule_key"]:
                        continue
                    if feedback["scope"] == "finding" and (
                        feedback["finding_fingerprint"] == finding["fingerprint"]
                    ):
                        return True
                    if feedback["scope"] == "title" and feedback["title_id"] == finding.get("title_id"):
                        return True
                    if feedback["scope"] == "source" and feedback["root_id"] == finding.get("root_id"):
                        return True
                return False

            suppressed_count = sum(1 for finding in candidates if is_suppressed(finding))
            candidates = [finding for finding in candidates if not is_suppressed(finding)]
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
            weights = {
                "critical": calibration["critical_weight"],
                "warning": calibration["warning_weight"],
                "information": calibration["information_weight"],
            }
            scored_findings = [
                dict(row) for row in conn.execute(
                    "SELECT category,severity FROM mie_findings WHERE status='active'"
                )
            ]
            category_scores = []
            for category in sorted(CATEGORIES):
                counts = Counter(
                    finding["severity"] for finding in scored_findings
                    if finding["category"] == category
                )
                score = max(0, 100 - sum(counts[level] * weight for level, weight in weights.items()))
                category_scores.append((category, score, counts))
            overall_score = round(
                sum(score for _, score, _ in category_scores) / len(category_scores)
            )
            cursor = conn.execute(
                """INSERT INTO mie_analysis_runs(
                     analyzed_at,active_findings,suppressed_findings,overall_score
                   ) VALUES (?,?,?,?)""",
                (analyzed_at, len(scored_findings), suppressed_count, overall_score),
            )
            run_id = cursor.lastrowid
            conn.executemany(
                """INSERT INTO mie_category_scores(
                     run_id,category,score,critical_count,warning_count,information_count
                   ) VALUES (?,?,?,?,?,?)""",
                [
                    (run_id, category, score, counts["critical"], counts["warning"], counts["information"])
                    for category, score, counts in category_scores
                ],
            )
            conn.execute(
                """DELETE FROM mie_analysis_runs WHERE id NOT IN (
                     SELECT id FROM mie_analysis_runs ORDER BY id DESC LIMIT 50
                   )"""
            )
        return len(candidates)

    def identity_report(self, title_id: int) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            title_row = conn.execute(
                """SELECT t.*,r.label root_label,r.path root_path FROM titles t
                   JOIN roots r ON r.id=t.root_id WHERE t.id=?""", (title_id,),
            ).fetchone()
            if not title_row:
                return None
            files = [dict(row) for row in conn.execute(
                "SELECT * FROM files WHERE title_id=? ORDER BY season,episode_start,filename COLLATE NOCASE",
                (title_id,),
            )]
        title = dict(title_row)
        report = _identity_evidence(title, files)
        report.update({"title": title, "files": files})
        return report

    def storage_report(self) -> dict[str, Any]:
        with self.database.connect() as conn:
            by_source = [dict(row) for row in conn.execute(
                """SELECT r.id,r.label,r.kind,COUNT(DISTINCT t.id) titles,COUNT(f.id) files,
                          COALESCE(SUM(f.size_bytes),0) bytes
                   FROM roots r LEFT JOIN titles t ON t.root_id=r.id
                   LEFT JOIN files f ON f.title_id=t.id GROUP BY r.id ORDER BY bytes DESC"""
            )]
            by_kind = [dict(row) for row in conn.execute(
                """SELECT t.kind,COUNT(DISTINCT t.id) titles,COUNT(f.id) files,
                          COALESCE(SUM(f.size_bytes),0) bytes
                   FROM titles t LEFT JOIN files f ON f.title_id=t.id GROUP BY t.kind ORDER BY bytes DESC"""
            )]
            largest_titles = [dict(row) for row in conn.execute(
                """SELECT t.id,COALESCE(t.metadata_title,t.title) title,t.kind,COUNT(f.id) files,
                          COALESCE(SUM(f.size_bytes),0) bytes
                   FROM titles t JOIN files f ON f.title_id=t.id GROUP BY t.id ORDER BY bytes DESC LIMIT 20"""
            )]
            largest_folders = [dict(row) for row in conn.execute(
                """SELECT t.folder_path,COALESCE(t.metadata_title,t.title) title,t.kind,
                          COUNT(f.id) files,COALESCE(SUM(f.size_bytes),0) bytes
                   FROM titles t JOIN files f ON f.title_id=t.id GROUP BY t.id ORDER BY bytes DESC LIMIT 20"""
            )]
            cleanup_trend = [dict(row) for row in conn.execute(
                """SELECT day,SUM(bytes) bytes,SUM(files) files FROM (
                     SELECT substr(COALESCE(purged_at,moved_at),1,10) day,size_bytes bytes,1 files
                     FROM duplicate_trash WHERE status='purged'
                     UNION ALL SELECT substr(verified_at,1,10),size_bytes,1 FROM duplicate_manual_removals
                   ) GROUP BY day ORDER BY day DESC LIMIT 30"""
            )]
        return {"by_source": by_source, "by_kind": by_kind, "largest_titles": largest_titles,
                "largest_folders": largest_folders, "cleanup_trend": cleanup_trend}

    def calibration(self) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM mie_calibration WHERE id=1").fetchone()
        result = dict(DEFAULT_CALIBRATION)
        if row:
            result.update({key: row[key] for key in DEFAULT_CALIBRATION})
            result["updated_at"] = row["updated_at"]
        else:
            result["updated_at"] = None
        return result

    def save_calibration(
        self, *, identity_warning_threshold: str, source_stale_hours: str,
        critical_weight: str, warning_weight: str, information_weight: str,
        user_id: int | None = None,
    ) -> None:
        def whole_number(value: str, label: str, minimum: int, maximum: int) -> int:
            try:
                parsed = int(value.strip())
            except (AttributeError, ValueError) as exc:
                raise ValueError(f"{label} must be a whole number.") from exc
            if parsed < minimum or parsed > maximum:
                raise ValueError(f"{label} must be between {minimum} and {maximum}.")
            return parsed

        values = (
            whole_number(identity_warning_threshold, "Identity warning threshold", 1, 100),
            whole_number(source_stale_hours, "Source stale time", 1, 8760),
            whole_number(critical_weight, "Critical score penalty", 1, 100),
            whole_number(warning_weight, "Warning score penalty", 1, 100),
            whole_number(information_weight, "Information score penalty", 0, 100),
        )
        if not values[2] >= values[3] >= values[4]:
            raise ValueError(
                "Score penalties must descend from Critical to Warning to Information."
            )
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO mie_calibration(
                     id,identity_warning_threshold,source_stale_hours,
                     critical_weight,warning_weight,information_weight,updated_by
                   ) VALUES (1,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     identity_warning_threshold=excluded.identity_warning_threshold,
                     source_stale_hours=excluded.source_stale_hours,
                     critical_weight=excluded.critical_weight,
                     warning_weight=excluded.warning_weight,
                     information_weight=excluded.information_weight,
                     updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                (*values, user_id if user_id and user_id > 0 else None),
            )

    def category_scores(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT cs.* FROM mie_category_scores cs
                   WHERE cs.run_id=(SELECT MAX(id) FROM mie_analysis_runs)
                   ORDER BY cs.category"""
            ).fetchall()
        return [dict(row) for row in rows]

    def analysis_history(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM mie_analysis_runs
                   ORDER BY id DESC LIMIT ?""", (max(1, min(limit, 50)),)
            ).fetchall()
        return [dict(row) for row in rows]

    def feedback(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT fb.*,COALESCE(t.metadata_title,t.title) title_name,
                          COALESCE(r.label,r.path) root_name
                   FROM mie_feedback fb
                   LEFT JOIN titles t ON t.id=fb.title_id
                   LEFT JOIN roots r ON r.id=fb.root_id
                   WHERE fb.active=1 ORDER BY fb.created_at DESC,fb.id DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_feedback(self, feedback_id: int) -> bool:
        with self.database.connect() as conn:
            feedback = conn.execute(
                "SELECT finding_fingerprint FROM mie_feedback WHERE id=? AND active=1",
                (feedback_id,),
            ).fetchone()
            if not feedback:
                return False
            cursor = conn.execute(
                "UPDATE mie_feedback SET active=0 WHERE id=? AND active=1", (feedback_id,)
            )
            conn.execute(
                """UPDATE mie_findings
                   SET status='active',dismissed_at=NULL,dismissed_by=NULL
                   WHERE fingerprint=?""",
                (feedback["finding_fingerprint"],),
            )
        return bool(cursor.rowcount)

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
            latest_run = conn.execute(
                "SELECT overall_score,suppressed_findings FROM mie_analysis_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "active": int(row["active"] or 0),
            "critical": int(row["critical"] or 0),
            "warning": int(row["warning"] or 0),
            "dismissed": int(row["dismissed"] or 0),
            "last_analyzed_at": state["last_analyzed_at"] if state else None,
            "overall_score": latest_run["overall_score"] if latest_run else None,
            "suppressed": latest_run["suppressed_findings"] if latest_run else 0,
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
            finding["review_label"] = {
                "missing-episodes": "Review missing episodes",
                "identity-confidence-low": "Review identity match",
                "unmatched-title": "Review match options",
                "duplicate-candidates": "Review duplicate candidates",
                "duplicate-storage-recovery": "Review recoverable storage",
                "media-unreadable": "Review unreadable media",
                "technical-details-missing": "Review media inspection",
                "source-stale": "Review source scan",
                "source-offline": "Review source connection",
                "source-degraded": "Review protected catalog",
                "metadata-artwork-missing": "Review artwork",
                "metadata-credits-missing": "Refresh credits",
                "metadata-episodes-incomplete": "Refresh episode data",
                "metadata-stale": "Refresh metadata",
                "metadata-identifiers-missing": "Review provider match",
            }.get(finding["rule_key"], "Review affected media")
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
            return f"/titles/{finding['title_id']}/identity"
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

    def dismiss(
        self, finding_id: int, user_id: int | None, *, reason: str = "other",
        scope: str = "finding", note: str = "",
    ) -> bool:
        reason = reason.strip().casefold()
        scope = scope.strip().casefold()
        note = note.strip()
        if reason not in FEEDBACK_REASONS:
            raise ValueError("Choose a valid reason for dismissing this finding.")
        if scope not in FEEDBACK_SCOPES:
            raise ValueError("Choose whether this feedback applies to the finding, title, or source.")
        if len(note) > 500:
            raise ValueError("Feedback notes must be 500 characters or fewer.")
        with self.database.connect() as conn:
            finding = conn.execute(
                "SELECT * FROM mie_findings WHERE id=? AND status='active'", (finding_id,)
            ).fetchone()
            if not finding:
                return False
            if scope == "title" and finding["title_id"] is None:
                raise ValueError("This finding is not tied to a title. Choose Finding only or Source.")
            if scope == "source" and finding["root_id"] is None:
                raise ValueError("This finding is not tied to a source. Choose Finding only or Title.")
            conn.execute(
                """INSERT INTO mie_feedback(
                     finding_fingerprint,rule_key,root_id,title_id,file_id,
                     reason,scope,note,created_by
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    finding["fingerprint"], finding["rule_key"], finding["root_id"],
                    finding["title_id"], finding["file_id"], reason, scope, note,
                    user_id if user_id and user_id > 0 else None,
                ),
            )
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
            finding = conn.execute(
                "SELECT fingerprint FROM mie_findings WHERE id=? AND status='dismissed'",
                (finding_id,),
            ).fetchone()
            if not finding:
                return False
            conn.execute(
                "UPDATE mie_feedback SET active=0 WHERE finding_fingerprint=? AND active=1",
                (finding["fingerprint"],),
            )
            cursor = conn.execute(
                """UPDATE mie_findings
                   SET status='active',dismissed_at=NULL,dismissed_by=NULL
                   WHERE id=? AND status='dismissed'""",
                (finding_id,),
            )
        return bool(cursor.rowcount)
