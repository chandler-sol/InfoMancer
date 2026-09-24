from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..db import Database
from .deep import (
    DeepCandidatePolicy,
    DeepCorrelationPolicy,
    build_deep_plan_metadata,
    generate_deep_episode_candidates,
    plan_deep_correlation,
)
from .deep_speech import (
    DeepSpeechPlan,
    DeepSpeechPolicy,
    build_deep_speech_plan,
)
from .decision_snapshot import result_revision
from .models import MediaIdentityFile
from .service import MediaIdentityDecisionService
from .speech import (
    SpeechAudioIdentity,
    SpeechEngine,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechWindow,
)
from .speech_audio import (
    LocalFfmpegSpeechAudioExtractor,
    SPEECH_AUDIO_POLICY_VERSION,
    validate_speech_audio_budget,
    validate_speech_window_plan,
)
from .speech_service import (
    NORMAL_SPEECH_ARTIFACT_KEY,
    NORMAL_SPEECH_ARTIFACT_VERSION,
    NormalSpeechObservation,
    NormalSpeechRun,
    NormalSpeechService,
    NormalSpeechStaleError,
    _transcript_output_is_sealed,
)
from .versions import (
    DEEP_ORCHESTRATION_VERSION,
    DEEP_SPEECH_SAMPLING_VERSION,
)


DEEP_SPEECH_MANIFEST_KEY = "deep-speech-sampling"
DEEP_SPEECH_MANIFEST_VERSION = str(DEEP_SPEECH_SAMPLING_VERSION)


class DeepSpeechSamplingError(RuntimeError):
    """Raised when Deep speech cannot safely resume or publish completion."""


@dataclass(frozen=True)
class DeepSpeechSamplingRun:
    scan_id: int
    plan_signature: str
    candidate_plan_signature: str
    correlation_plan_signature: str
    planned_window_count: int
    transcript_count: int
    text_transcript_count: int
    reused_artifact_count: int
    manifest_artifact_id: int | None
    coverage_complete: bool
    failures: tuple[str, ...]
    budget_exhausted: bool


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeepSpeechSamplingError(
            "Deep speech metadata could not be serialized safely."
        ) from exc


def _json_object(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _same_modified_at(first: object, second: object) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return float(first) == float(second)


def _valid_sha256(value: object) -> bool:
    text = str(value or "").strip().casefold()
    return (
        len(text) == 64
        and all(character in "0123456789abcdef" for character in text)
    )


class DeepSpeechService(NormalSpeechService):
    """Normal's proven transcription engine with a larger sealed Deep policy."""

    artifact_profile = "deep"
    profile_label = "Deep"
    require_transcript_output_seal = True

    def __init__(
        self,
        database: Database,
        engine: SpeechEngine,
        model: SpeechModelIdentity,
        *,
        policy: DeepSpeechPolicy | None = None,
        extractor_factory: Callable[..., LocalFfmpegSpeechAudioExtractor] = (
            LocalFfmpegSpeechAudioExtractor
        ),
        language: str = "",
        translate: bool = False,
        synopsis_language: str | None = None,
        preferred_audio_language: str | None = None,
        translation_target_language: str | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        self.deep_policy = policy or DeepSpeechPolicy()
        super().__init__(
            database,
            engine,
            model,
            extractor_factory=extractor_factory,
            language=language,
            translate=translate,
            synopsis_language=synopsis_language,
            preferred_audio_language=preferred_audio_language,
            translation_target_language=translation_target_language,
            parameters=parameters,
        )

    def plan_speech_windows(self, runtime_seconds: Any) -> tuple[SpeechWindow, ...]:
        return build_deep_speech_plan(
            runtime_seconds,
            policy=self.deep_policy,
        ).windows

    def validate_window_plan(
        self,
        windows: Iterable[SpeechWindow],
    ) -> tuple[SpeechWindow, ...]:
        return validate_speech_window_plan(
            windows,
            max_windows=self.deep_policy.max_windows,
            max_total_ms=self.deep_policy.max_total_ms,
            profile_label="Deep",
        )

    def validate_audio_budget(
        self,
        records: Iterable[tuple[SpeechWindow, SpeechAudioIdentity]],
    ) -> tuple[tuple[SpeechWindow, SpeechAudioIdentity], ...]:
        return validate_speech_audio_budget(
            records,
            max_windows=self.deep_policy.max_windows,
            max_total_ms=self.deep_policy.max_total_ms,
            max_total_bytes=self.deep_policy.max_audio_bytes,
            profile_label="Deep",
        )

    def _require_fresh_scan(
        self,
        scan_id: int,
        baseline_scan: Mapping[str, Any],
        media: MediaIdentityFile | None = None,
    ) -> dict[str, Any]:
        current = super()._require_fresh_scan(
            scan_id,
            baseline_scan,
            media,
        )
        if result_revision(current) != result_revision(baseline_scan):
            raise NormalSpeechStaleError(
                "Episode Identity publication changed during Deep speech analysis."
            )
        return current


class DeepSpeechSamplingService:
    """Persist a completion manifest over exact resumable Deep transcript artifacts."""

    def __init__(
        self,
        database: Database,
        engine: SpeechEngine,
        model: SpeechModelIdentity,
        *,
        policy: DeepSpeechPolicy | None = None,
        candidate_policy: DeepCandidatePolicy | None = None,
        correlation_policy: DeepCorrelationPolicy | None = None,
        extractor_factory: Callable[..., LocalFfmpegSpeechAudioExtractor] = (
            LocalFfmpegSpeechAudioExtractor
        ),
        language: str = "",
        translate: bool = False,
        synopsis_language: str | None = None,
        preferred_audio_language: str | None = None,
        translation_target_language: str | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> None:
        self.database = database
        self.engine = engine
        self.model = model
        self.policy = policy or DeepSpeechPolicy()
        self.candidate_policy = candidate_policy or DeepCandidatePolicy()
        self.correlation_policy = correlation_policy or DeepCorrelationPolicy()
        self.extractor_factory = extractor_factory
        self.language = language
        self.translate = translate
        self.synopsis_language = synopsis_language
        self.preferred_audio_language = preferred_audio_language
        self.translation_target_language = translation_target_language
        self.parameters = dict(parameters or {})

    def _service(self) -> DeepSpeechService:
        return DeepSpeechService(
            self.database,
            self.engine,
            self.model,
            policy=self.policy,
            extractor_factory=self.extractor_factory,
            language=self.language,
            translate=self.translate,
            synopsis_language=self.synopsis_language,
            preferred_audio_language=self.preferred_audio_language,
            translation_target_language=self.translation_target_language,
            parameters=self.parameters,
        )

    @staticmethod
    def _scan_bundle(
        conn: sqlite3.Connection,
        scan_id: int,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, Any],
        list[dict[str, Any]],
    ]:
        scan_row = conn.execute(
            "SELECT * FROM media_identity_scans WHERE id=?",
            (int(scan_id),),
        ).fetchone()
        if scan_row is None:
            raise DeepSpeechSamplingError(
                "Episode Identity scan was not found."
            )
        scan = dict(scan_row)
        evidence = [
            dict(row)
            for row in conn.execute(
                """SELECT * FROM media_identity_evidence
                   WHERE scan_id=? ORDER BY id""",
                (int(scan_id),),
            ).fetchall()
        ]
        file_row = conn.execute(
            """SELECT f.*,t.kind title_kind
               FROM files f
               JOIN titles t ON t.id=f.title_id
               WHERE f.id=?""",
            (int(scan["file_id"]),),
        ).fetchone()
        if file_row is None:
            raise DeepSpeechSamplingError(
                "Episode Identity media disappeared before Deep speech."
            )
        streams = [
            dict(row)
            for row in conn.execute(
                """SELECT stream_index,stream_type,codec,language,title,channels,
                          channel_layout,sample_rate,default_flag,forced_flag,
                          hearing_impaired,visual_impaired,commentary,
                          disposition_json
                   FROM media_streams
                   WHERE file_id=? ORDER BY stream_index""",
                (int(scan["file_id"]),),
            ).fetchall()
        ]
        return scan, evidence, dict(file_row), streams

    @staticmethod
    def _media(
        scan: Mapping[str, Any],
        file_row: Mapping[str, Any],
    ) -> MediaIdentityFile:
        return MediaIdentityFile(
            file_id=int(scan["file_id"]),
            title_id=int(file_row["title_id"]),
            path=str(file_row["path"]),
            size_bytes=int(scan["file_size_bytes"] or 0),
            modified_at=scan["file_modified_at"],
            sha256=str(scan["file_sha256"] or "") or None,
        )

    def _deep_identity(
        self,
        conn: sqlite3.Connection,
        scan: Mapping[str, Any],
        file_row: Mapping[str, Any],
    ) -> dict[str, Any]:
        claimed = _json_object(scan["claimed_identity_json"])
        language = str(
            claimed.get("scan_language") or "eng"
        ).strip().casefold() or "eng"
        candidates = generate_deep_episode_candidates(
            conn,
            title_id=int(file_row["title_id"]),
            season=int(file_row["season"]),
            episode_start=int(file_row["episode_start"]),
            episode_end=int(
                file_row["episode_end"] or file_row["episode_start"]
            ),
            language=language,
            policy=self.candidate_policy,
        )
        correlation = plan_deep_correlation(
            conn,
            file_id=int(scan["file_id"]),
            policy=self.correlation_policy,
        )
        return build_deep_plan_metadata(candidates, correlation)

    @staticmethod
    def _engine_identity(
        service: DeepSpeechService,
    ) -> dict[str, Any]:
        snapshot = service._engine_snapshot()
        return {
            "key": snapshot.key,
            "version": snapshot.version,
            "binary": {
                **dict(snapshot.binary.cache_identity()),
                "source": snapshot.binary.source,
                "license_id": snapshot.binary.license_id,
                "details": snapshot.binary.details_payload(),
            },
            "identity": dict(snapshot.identity),
        }

    def _manifest_identity(
        self,
        scan: Mapping[str, Any],
        *,
        revision: int,
        plan: DeepSpeechPlan,
        deep_identity: Mapping[str, Any],
        service: DeepSpeechService,
    ) -> dict[str, Any]:
        return {
            "deep_orchestration_version": DEEP_ORCHESTRATION_VERSION,
            "deep_speech_sampling_version": DEEP_SPEECH_SAMPLING_VERSION,
            "speech_audio_policy_version": SPEECH_AUDIO_POLICY_VERSION,
            "speech_artifact_version": NORMAL_SPEECH_ARTIFACT_VERSION,
            "scan_id": int(scan["id"]),
            "result_revision": int(revision),
            "file_id": int(scan["file_id"]),
            "file_size_bytes": int(scan["file_size_bytes"] or 0),
            "file_modified_at": scan["file_modified_at"],
            "file_sha256": str(scan["file_sha256"] or ""),
            "metadata_signature": str(scan["metadata_signature"] or ""),
            "candidate_plan_signature": str(
                deep_identity.get("candidate_plan_signature") or ""
            ),
            "correlation_plan_signature": str(
                deep_identity.get("correlation_plan_signature") or ""
            ),
            "speech_policy": plan.policy.identity_payload(),
            "speech_plan_signature": plan.plan_signature,
            "engine": self._engine_identity(service),
            "model": {
                **dict(self.model.cache_identity()),
                "source": self.model.source,
                "license_id": self.model.license_id,
                "details": self.model.details_payload(),
            },
            "configuration": {
                "synopsis_language": service.synopsis_language,
                "preferred_audio_language": service.preferred_audio_language,
                "translation_target_language": (
                    service.translation_target_language
                ),
                "translate": bool(service.translate),
                "parameters": dict(service.parameters),
            },
        }

    @staticmethod
    def _manifest_cache_key(identity: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            _canonical_json(dict(identity)).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _artifact_matches_manifest(
        row: Mapping[str, Any],
        *,
        scan: Mapping[str, Any],
        sample,
        item: Mapping[str, Any],
    ) -> bool:
        if (
            int(row.get("id") or 0) != int(item.get("artifact_id") or 0)
            or str(row.get("artifact_type") or "") != "speech_transcript"
            or str(row.get("analyzer_key") or "") != NORMAL_SPEECH_ARTIFACT_KEY
            or str(row.get("analyzer_version") or "")
            != NORMAL_SPEECH_ARTIFACT_VERSION
            or str(row.get("status") or "") != "complete"
            or str(row.get("profile") or "") not in {"normal", "deep"}
            or str(row.get("cache_key") or "") != str(item.get("cache_key") or "")
            or int(row.get("file_id") or 0) != int(scan["file_id"])
            or int(row.get("file_size_bytes") or 0)
            != int(scan["file_size_bytes"] or 0)
            or not _same_modified_at(
                row.get("file_modified_at"),
                scan.get("file_modified_at"),
            )
            or row.get("start_ms") is None
            or row.get("end_ms") is None
            or int(row["start_ms"]) != int(sample.window.start_ms)
            or int(row["end_ms"]) != int(sample.window.end_ms)
            or int(item.get("ordinal") or 0) != int(sample.ordinal)
            or str(item.get("work_key") or "") != sample.work_key
        ):
            return False

        payload = _json_object(row.get("payload_json"))
        if not _transcript_output_is_sealed(
            row.get("text_value"),
            payload,
        ):
            return False
        seal = str(
            payload.get("transcript_output_sha256") or ""
        ).strip().casefold()
        if seal != str(item.get("transcript_output_sha256") or ""):
            return False
        audio = payload.get("audio_identity")
        if not isinstance(audio, Mapping):
            return False
        if str(audio.get("sha256") or "") != str(item.get("audio_sha256") or ""):
            return False
        return True

    def _load_manifest(
        self,
        scan: Mapping[str, Any],
        *,
        identity: Mapping[str, Any],
        plan: DeepSpeechPlan,
    ) -> tuple[int, tuple[int, ...], int] | None:
        cache_key = self._manifest_cache_key(identity)
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='deep_speech_manifest'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(scan["file_id"]),
                    DEEP_SPEECH_MANIFEST_KEY,
                    DEEP_SPEECH_MANIFEST_VERSION,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                return None
            manifest = dict(row)
            payload = _json_object(manifest["payload_json"])
            if (
                str(manifest["status"] or "") != "complete"
                or str(manifest["profile"] or "") != "deep"
                or str(manifest["source_kind"] or "") != "local_speech"
                or str(manifest["source_signature"] or "")
                != plan.plan_signature
                or int(manifest["file_size_bytes"] or 0)
                != int(scan["file_size_bytes"] or 0)
                or not _same_modified_at(
                    manifest["file_modified_at"],
                    scan["file_modified_at"],
                )
                or payload.get("identity") != dict(identity)
                or payload.get("coverage_complete") is not True
            ):
                raise DeepSpeechSamplingError(
                    "The persisted Deep speech manifest failed provenance validation."
                )
            raw_items = payload.get("observations")
            if (
                not isinstance(raw_items, list)
                or len(raw_items) != len(plan.samples)
            ):
                raise DeepSpeechSamplingError(
                    "The persisted Deep speech manifest is incomplete."
                )

            artifact_ids: list[int] = []
            text_transcript_count = 0
            for sample, item in zip(plan.samples, raw_items):
                if not isinstance(item, Mapping):
                    raise DeepSpeechSamplingError(
                        "The persisted Deep speech manifest is malformed."
                    )
                try:
                    artifact_id = int(item["artifact_id"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DeepSpeechSamplingError(
                        "The persisted Deep speech manifest is malformed."
                    ) from exc
                child = conn.execute(
                    "SELECT * FROM media_identity_artifacts WHERE id=?",
                    (artifact_id,),
                ).fetchone()
                if child is None or not self._artifact_matches_manifest(
                    dict(child),
                    scan=scan,
                    sample=sample,
                    item=item,
                ):
                    raise DeepSpeechSamplingError(
                        "The persisted Deep speech manifest failed artifact "
                        "integrity validation."
                    )
                artifact_ids.append(artifact_id)
                if str(child["text_value"] or "").strip():
                    text_transcript_count += 1

            conn.execute(
                """UPDATE media_identity_artifacts
                   SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                (int(manifest["id"]),),
            )
            return (
                int(manifest["id"]),
                tuple(artifact_ids),
                text_transcript_count,
            )

    def _persist_manifest(
        self,
        scan: Mapping[str, Any],
        *,
        baseline_revision: int,
        identity: Mapping[str, Any],
        plan: DeepSpeechPlan,
        deep_identity: Mapping[str, Any],
        run: NormalSpeechRun,
    ) -> int:
        if len(run.observations) != len(plan.samples):
            raise DeepSpeechSamplingError(
                "Deep speech completion cannot publish partial coverage."
            )
        payload = {
            "identity": dict(identity),
            "deep_identity": dict(deep_identity),
            "coverage_complete": True,
            "observations": [],
        }
        for sample, observation in zip(plan.samples, run.observations):
            if (
                observation.window.start_ms != sample.window.start_ms
                or observation.window.end_ms != sample.window.end_ms
            ):
                raise DeepSpeechSamplingError(
                    "Deep speech observations do not match the planned window order."
                )
            with self.database.connect() as read_conn:
                row = read_conn.execute(
                    "SELECT * FROM media_identity_artifacts WHERE id=?",
                    (int(observation.artifact_id),),
                ).fetchone()
            if row is None:
                raise DeepSpeechSamplingError(
                    "A Deep speech transcript disappeared before publication."
                )
            row_dict = dict(row)
            artifact_payload = _json_object(row_dict["payload_json"])
            if not _transcript_output_is_sealed(
                row_dict["text_value"],
                artifact_payload,
            ):
                raise DeepSpeechSamplingError(
                    "Deep speech requires sealed transcript outputs."
                )
            audio = artifact_payload.get("audio_identity")
            if not isinstance(audio, Mapping):
                raise DeepSpeechSamplingError(
                    "Deep speech transcript audio provenance is incomplete."
                )
            payload["observations"].append({
                "ordinal": sample.ordinal,
                "work_key": sample.work_key,
                "artifact_id": int(observation.artifact_id),
                "cache_key": observation.cache_key,
                "transcript_output_sha256": str(
                    artifact_payload["transcript_output_sha256"]
                ),
                "audio_sha256": str(audio.get("sha256") or ""),
                "start_ms": sample.window.start_ms,
                "end_ms": sample.window.end_ms,
                "inherited_normal": sample.inherited_normal,
            })

        cache_key = self._manifest_cache_key(identity)
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current, evidence, file_row, _ = self._scan_bundle(
                conn,
                int(scan["id"]),
            )
            snapshot_current, _ = MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                current,
                evidence,
            )
            if (
                not snapshot_current
                or result_revision(current) != int(baseline_revision)
                or str(current["metadata_signature"] or "")
                != str(scan["metadata_signature"] or "")
                or str(current["file_sha256"] or "")
                != str(scan["file_sha256"] or "")
            ):
                raise DeepSpeechSamplingError(
                    "Episode Identity publication changed before Deep speech "
                    "completion."
                )
            current_deep_identity = self._deep_identity(
                conn,
                current,
                file_row,
            )
            if dict(current_deep_identity) != dict(deep_identity):
                raise DeepSpeechSamplingError(
                    "Deep candidate or correlation inputs changed during speech."
                )

            raw_observations = payload.get("observations")
            if not isinstance(raw_observations, list):
                raise DeepSpeechSamplingError(
                    "Deep speech completion metadata is malformed."
                )
            for sample, item in zip(plan.samples, raw_observations):
                if not isinstance(item, Mapping):
                    raise DeepSpeechSamplingError(
                        "Deep speech completion metadata is malformed."
                    )
                child = conn.execute(
                    "SELECT * FROM media_identity_artifacts WHERE id=?",
                    (int(item["artifact_id"]),),
                ).fetchone()
                if child is None or not self._artifact_matches_manifest(
                    dict(child),
                    scan=current,
                    sample=sample,
                    item=item,
                ):
                    raise DeepSpeechSamplingError(
                        "A Deep speech transcript changed before manifest publication."
                    )

            cursor = conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     payload_json
                   ) VALUES (
                     ?,'deep_speech_manifest',?,?,?,'complete','deep',
                     'local_speech',?,?,?,?,?,?
                   )""",
                (
                    int(scan["file_id"]),
                    DEEP_SPEECH_MANIFEST_KEY,
                    DEEP_SPEECH_MANIFEST_VERSION,
                    cache_key,
                    f"scan:{int(scan['id'])}",
                    plan.plan_signature,
                    int(scan["file_size_bytes"] or 0),
                    scan["file_modified_at"],
                    _canonical_json(payload),
                ),
            )
            inserted = cursor.rowcount == 1
            row = conn.execute(
                """SELECT * FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='deep_speech_manifest'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(scan["file_id"]),
                    DEEP_SPEECH_MANIFEST_KEY,
                    DEEP_SPEECH_MANIFEST_VERSION,
                    cache_key,
                ),
            ).fetchone()
            if row is None:
                raise DeepSpeechSamplingError(
                    "InfoMancer could not persist the Deep speech manifest."
                )
            persisted = dict(row)
            if (
                not inserted
                and (
                    str(persisted["status"] or "") != "complete"
                    or str(persisted["profile"] or "") != "deep"
                    or str(persisted["source_kind"] or "") != "local_speech"
                    or str(persisted["source_ref"] or "")
                    != f"scan:{int(scan['id'])}"
                    or str(persisted["source_signature"] or "")
                    != plan.plan_signature
                    or _json_object(persisted["payload_json"]) != payload
                )
            ):
                raise DeepSpeechSamplingError(
                    "A conflicting Deep speech manifest already exists."
                )
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET last_used_at=CURRENT_TIMESTAMP WHERE id=?""",
                (int(persisted["id"]),),
            )
            return int(persisted["id"])

    def run(self, scan_id: int) -> DeepSpeechSamplingRun:
        with self.database.connect() as conn:
            scan, evidence, file_row, streams = self._scan_bundle(
                conn,
                int(scan_id),
            )
            if (
                scan["status"] != "complete"
                or str(file_row.get("title_kind") or "") != "tv"
            ):
                raise DeepSpeechSamplingError(
                    "Deep speech requires a complete TV Episode Identity scan."
                )
            snapshot_current, _ = MediaIdentityDecisionService._scan_snapshot_is_current(
                conn,
                scan,
                evidence,
            )
            if not snapshot_current:
                raise DeepSpeechSamplingError(
                    "The Episode Identity snapshot is stale."
                )
            baseline_revision = result_revision(scan)
            if baseline_revision <= 0:
                raise DeepSpeechSamplingError(
                    "Deep speech requires a sealed Episode Identity result."
                )
            if not _valid_sha256(scan["file_sha256"]):
                raise DeepSpeechSamplingError(
                    "Deep speech requires an exact media SHA-256 snapshot."
                )
            runtime_seconds = file_row.get("runtime_seconds")
            plan = build_deep_speech_plan(
                runtime_seconds,
                policy=self.policy,
            )
            if not plan.samples:
                return DeepSpeechSamplingRun(
                    scan_id=int(scan_id),
                    plan_signature=plan.plan_signature,
                    candidate_plan_signature="",
                    correlation_plan_signature="",
                    planned_window_count=0,
                    transcript_count=0,
                    text_transcript_count=0,
                    reused_artifact_count=0,
                    manifest_artifact_id=None,
                    coverage_complete=False,
                    failures=("speech-runtime-unavailable",),
                    budget_exhausted=False,
                )
            deep_identity = self._deep_identity(conn, scan, file_row)
            media = self._media(scan, file_row)

        service = self._service()
        try:
            if not self.engine.available():
                return DeepSpeechSamplingRun(
                    scan_id=int(scan_id),
                    plan_signature=plan.plan_signature,
                    candidate_plan_signature=str(
                        deep_identity["candidate_plan_signature"]
                    ),
                    correlation_plan_signature=str(
                        deep_identity["correlation_plan_signature"]
                    ),
                    planned_window_count=len(plan.samples),
                    transcript_count=0,
                    text_transcript_count=0,
                    reused_artifact_count=0,
                    manifest_artifact_id=None,
                    coverage_complete=False,
                    failures=("speech-engine-unavailable",),
                    budget_exhausted=False,
                )
        except Exception as exc:
            return DeepSpeechSamplingRun(
                scan_id=int(scan_id),
                plan_signature=plan.plan_signature,
                candidate_plan_signature=str(
                    deep_identity["candidate_plan_signature"]
                ),
                correlation_plan_signature=str(
                    deep_identity["correlation_plan_signature"]
                ),
                planned_window_count=len(plan.samples),
                transcript_count=0,
                text_transcript_count=0,
                reused_artifact_count=0,
                manifest_artifact_id=None,
                coverage_complete=False,
                failures=(
                    f"speech-engine-unavailable:{type(exc).__name__}",
                ),
                budget_exhausted=False,
            )

        identity = self._manifest_identity(
            scan,
            revision=baseline_revision,
            plan=plan,
            deep_identity=deep_identity,
            service=service,
        )
        existing = self._load_manifest(
            scan,
            identity=identity,
            plan=plan,
        )
        if existing is not None:
            manifest_id, artifact_ids, text_transcript_count = existing
            return DeepSpeechSamplingRun(
                scan_id=int(scan_id),
                plan_signature=plan.plan_signature,
                candidate_plan_signature=str(
                    deep_identity["candidate_plan_signature"]
                ),
                correlation_plan_signature=str(
                    deep_identity["correlation_plan_signature"]
                ),
                planned_window_count=len(plan.samples),
                transcript_count=len(artifact_ids),
                text_transcript_count=text_transcript_count,
                reused_artifact_count=len(artifact_ids),
                manifest_artifact_id=manifest_id,
                coverage_complete=True,
                failures=(),
                budget_exhausted=False,
            )

        run = service.run(
            int(scan_id),
            scan,
            media,
            runtime_seconds,
            streams,
        )
        manifest_id = None
        if run.coverage_complete:
            manifest_id = self._persist_manifest(
                scan,
                baseline_revision=baseline_revision,
                identity=identity,
                plan=plan,
                deep_identity=deep_identity,
                run=run,
            )

        return DeepSpeechSamplingRun(
            scan_id=int(scan_id),
            plan_signature=plan.plan_signature,
            candidate_plan_signature=str(
                deep_identity["candidate_plan_signature"]
            ),
            correlation_plan_signature=str(
                deep_identity["correlation_plan_signature"]
            ),
            planned_window_count=len(plan.samples),
            transcript_count=run.transcript_count,
            text_transcript_count=run.text_transcript_count,
            reused_artifact_count=run.reused_artifact_count,
            manifest_artifact_id=manifest_id,
            coverage_complete=bool(
                run.coverage_complete and manifest_id is not None
            ),
            failures=tuple(run.failures),
            budget_exhausted=bool(run.budget_exhausted),
        )
