from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..db import Database
from .models import MediaIdentityFile
from .service import MediaIdentityDecisionService
from .speech import (
    MAX_NORMAL_SPEECH_TOTAL_MS,
    MAX_NORMAL_SPEECH_WINDOWS,
    SpeechAudioIdentity,
    SpeechBinaryIdentity,
    SpeechEngine,
    SpeechIdentityError,
    SpeechModelIdentity,
    SpeechRequest,
    SpeechTranscript,
    SpeechWindow,
    speech_transcript_cache_key,
)
from .speech_audio import (
    ExtractedSpeechAudio,
    LocalFfmpegSpeechAudioExtractor,
    SpeechAudioStaleError,
    SpeechAudioUnavailable,
    SpeechBudgetExceeded,
    normalize_speech_language,
    validate_normal_speech_audio_budget,
    validate_normal_speech_window_plan,
)


NORMAL_SPEECH_ARTIFACT_KEY = "local-speech-transcript"
NORMAL_SPEECH_ARTIFACT_VERSION = "1"
NORMAL_SPEECH_WINDOW_MS = 30_000
# Sample one 30-second window from each eighth of a long runtime. The
# order intentionally starts near the middle and fans outward so an interrupted
# run leaves a broadly useful partial transcript set without overlapping windows.
_NORMAL_SPEECH_SEGMENT_ORDER = (3, 1, 5, 0, 7, 2, 4, 6)


class NormalSpeechError(RuntimeError):
    """Base failure for Normal-profile speech orchestration."""


class NormalSpeechStaleError(NormalSpeechError):
    """Raised when the media or scan snapshot changes during speech work."""


@dataclass(frozen=True)
class _SpeechEngineSnapshot:
    key: str
    version: str
    binary: SpeechBinaryIdentity
    identity: Mapping[str, Any]

    def binary_identity(self) -> SpeechBinaryIdentity:
        return self.binary

    def cache_identity(self) -> Mapping[str, Any]:
        return self.identity


@dataclass(frozen=True)
class NormalSpeechObservation:
    window: SpeechWindow
    cache_key: str
    source_signature: str
    transcript: SpeechTranscript
    audio_identity: SpeechAudioIdentity
    artifact_id: int
    reused: bool = False


@dataclass(frozen=True)
class NormalSpeechRun:
    planned_windows: tuple[SpeechWindow, ...] = ()
    observations: tuple[NormalSpeechObservation, ...] = ()
    failures: tuple[str, ...] = ()
    budget_exhausted: bool = False
    synopsis_language: str = ""
    preferred_audio_language: str = ""
    selected_audio_language: str = ""
    transcription_input_language: str = ""
    translation_target_language: str = ""

    @property
    def reused_artifact_count(self) -> int:
        return sum(1 for item in self.observations if item.reused)

    @property
    def transcript_count(self) -> int:
        return len(self.observations)

    @property
    def text_transcript_count(self) -> int:
        return sum(1 for item in self.observations if item.transcript.text.strip())

    @property
    def coverage_complete(self) -> bool:
        if (
            not self.planned_windows
            or self.failures
            or self.budget_exhausted
            or len(self.observations) != len(self.planned_windows)
        ):
            return False
        expected = {
            (int(window.start_ms), int(window.end_ms))
            for window in self.planned_windows
        }
        observed = {
            (int(item.window.start_ms), int(item.window.end_ms))
            for item in self.observations
        }
        return observed == expected


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _same_modified_at(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return float(first) == float(second)


def plan_normal_speech_windows(runtime_seconds: Any) -> tuple[SpeechWindow, ...]:
    """Build a deterministic, bounded set of short dialogue samples.

    Files at or below the 240-second Normal speech budget are covered in
    non-overlapping chunks. Longer files use eight 30-second windows spread
    across the runtime in a deterministic priority order. The full plan is
    validated against the shared I1/I2 ceilings before any extraction begins.
    """
    if isinstance(runtime_seconds, bool):
        return ()
    try:
        runtime_value = float(runtime_seconds)
    except (TypeError, ValueError):
        return ()
    if not math.isfinite(runtime_value) or runtime_value <= 0:
        return ()

    runtime_ms = max(1, int(round(runtime_value * 1000.0)))
    if runtime_ms <= MAX_NORMAL_SPEECH_TOTAL_MS:
        windows: list[SpeechWindow] = []
        start = 0
        ordinal = 1
        while start < runtime_ms and len(windows) < MAX_NORMAL_SPEECH_WINDOWS:
            end = min(runtime_ms, start + NORMAL_SPEECH_WINDOW_MS)
            windows.append(
                SpeechWindow(
                    start_ms=start,
                    end_ms=end,
                    purpose=f"normal-target-{ordinal}",
                )
            )
            start = end
            ordinal += 1
        return validate_normal_speech_window_plan(windows)

    duration = min(NORMAL_SPEECH_WINDOW_MS, runtime_ms)
    planned: list[SpeechWindow] = []
    segment_width = runtime_ms / float(MAX_NORMAL_SPEECH_WINDOWS)
    for ordinal, segment_index in enumerate(
        _NORMAL_SPEECH_SEGMENT_ORDER,
        start=1,
    ):
        center = int(round((segment_index + 0.5) * segment_width))
        start = max(0, min(runtime_ms - duration, center - duration // 2))
        end = min(runtime_ms, start + duration)
        planned.append(
            SpeechWindow(
                start_ms=start,
                end_ms=end,
                purpose=f"normal-target-{ordinal}",
            )
        )

    return validate_normal_speech_window_plan(planned)


def _audio_identity_from_payload(payload: Mapping[str, Any]) -> SpeechAudioIdentity | None:
    raw = payload.get("audio_identity")
    if not isinstance(raw, Mapping):
        return None
    try:
        details = raw.get("details")
        return SpeechAudioIdentity(
            sha256=str(raw["sha256"]),
            size_bytes=int(raw["size_bytes"]),
            format_key=str(raw["format_key"]),
            sample_rate_hz=int(raw["sample_rate_hz"]),
            channels=int(raw["channels"]),
            source_signature=str(raw.get("source_signature") or ""),
            details=details if isinstance(details, Mapping) else {},
        )
    except (KeyError, TypeError, ValueError, SpeechIdentityError):
        return None


def _transcript_from_row(text_value: Any, payload: Mapping[str, Any]) -> SpeechTranscript | None:
    raw = payload.get("transcript")
    if not isinstance(raw, Mapping):
        raw = {}
    details = raw.get("details")
    try:
        return SpeechTranscript(
            text=str(text_value or ""),
            language=str(raw.get("language") or ""),
            confidence=raw.get("confidence"),
            details=details if isinstance(details, Mapping) else {},
        )
    except (TypeError, ValueError, SpeechIdentityError):
        return None


def _bounded_failure(prefix: str, exc: BaseException) -> str:
    detail = " ".join(str(exc).split())
    if len(detail) > 300:
        detail = detail[:297] + "..."
    name = type(exc).__name__
    return f"{prefix}:{name}" + (f":{detail}" if detail else "")


class NormalSpeechService:
    """Run resumable, bounded local speech transcription for one Normal scan.

    This layer deliberately persists transcripts only. It does not turn speech
    into candidate evidence or change identity scoring; that remains an I5
    responsibility.
    """

    def __init__(
        self,
        database: Database,
        engine: SpeechEngine,
        model: SpeechModelIdentity,
        *,
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
        if not isinstance(model, SpeechModelIdentity):
            raise SpeechIdentityError(
                "Normal speech orchestration requires an exact speech model identity."
            )
        if not isinstance(language, str):
            raise SpeechIdentityError("Normal speech language must be text.")
        for label, value in (
            ("synopsis language", synopsis_language),
            ("preferred audio language", preferred_audio_language),
            ("translation target language", translation_target_language),
        ):
            if value is not None and not isinstance(value, str):
                raise SpeechIdentityError(
                    f"Normal speech {label} must be text."
                )
        if not isinstance(translate, bool):
            raise SpeechIdentityError("Normal speech translation mode must be boolean.")
        if parameters is not None and not isinstance(parameters, Mapping):
            raise SpeechIdentityError("Normal speech parameters must be a mapping.")
        self.database = database
        self.engine = engine
        self.model = model
        self.extractor_factory = extractor_factory
        legacy_language = normalize_speech_language(language)
        self.synopsis_language = normalize_speech_language(
            language if synopsis_language is None else synopsis_language
        )
        self.preferred_audio_language = normalize_speech_language(
            language
            if preferred_audio_language is None
            else preferred_audio_language
        )
        raw_translation_target = (
            ""
            if translation_target_language is None
            else translation_target_language
        )
        self.translation_target_language = (
            ""
            if not raw_translation_target.strip()
            else normalize_speech_language(raw_translation_target)
        )
        if (
            self.translation_target_language
            and self.translation_target_language != "eng"
        ):
            raise SpeechIdentityError(
                "Normal speech currently supports translation to English only."
            )
        # Compatibility aliases for callers/tests using the pre-M06 names.
        self.language = legacy_language
        self.translate = translate
        self.parameters = dict(parameters or {})

    def _engine_snapshot(self) -> _SpeechEngineSnapshot:
        binary = self.engine.binary_identity()
        if not isinstance(binary, SpeechBinaryIdentity):
            raise SpeechIdentityError(
                "Speech engines require an exact binary identity."
            )
        raw_identity = self.engine.cache_identity()
        if not isinstance(raw_identity, Mapping):
            raise SpeechIdentityError(
                "Speech engines require a deterministic cache identity."
            )
        # Canonical JSON round-tripping both validates the identity payload and
        # detaches it from any mutable mapping owned by the live engine.
        identity = json.loads(_canonical_json(dict(raw_identity)))
        raw_key = getattr(self.engine, "key", "")
        raw_version = getattr(self.engine, "version", "")
        if not isinstance(raw_key, str) or not isinstance(raw_version, str):
            raise SpeechIdentityError(
                "Speech engines require stable text key and version values."
            )
        return _SpeechEngineSnapshot(
            key=raw_key,
            version=raw_version,
            binary=binary,
            identity=identity,
        )

    def _require_fresh_scan(
        self,
        scan_id: int,
        baseline_scan: Mapping[str, Any],
        media: MediaIdentityFile | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (int(scan_id),),
            ).fetchone()
            if not row:
                raise NormalSpeechStaleError(
                    "Episode Identity scan disappeared during speech analysis."
                )
            current_scan = dict(row)
            if current_scan["status"] != "complete":
                raise NormalSpeechStaleError(
                    "Episode Identity scan state changed during speech analysis."
                )
            if str(current_scan["metadata_signature"] or "") != str(
                baseline_scan.get("metadata_signature") or ""
            ):
                raise NormalSpeechStaleError(
                    "Episode Identity metadata changed during speech analysis."
                )
            if int(current_scan["file_id"]) != int(baseline_scan["file_id"]):
                raise NormalSpeechStaleError(
                    "Episode Identity file binding changed during speech analysis."
                )
            if int(current_scan["file_size_bytes"] or 0) != int(
                baseline_scan.get("file_size_bytes") or 0
            ):
                raise NormalSpeechStaleError(
                    "Episode Identity file size changed during speech analysis."
                )
            if not _same_modified_at(
                current_scan["file_modified_at"],
                baseline_scan.get("file_modified_at"),
            ):
                raise NormalSpeechStaleError(
                    "Episode Identity file timestamp changed during speech analysis."
                )
            if str(current_scan["file_sha256"] or "") != str(
                baseline_scan.get("file_sha256") or ""
            ):
                raise NormalSpeechStaleError(
                    "Episode Identity file hash binding changed during speech analysis."
                )

            current, current_file = MediaIdentityDecisionService._snapshot_is_current(
                conn,
                int(current_scan["file_id"]),
                size_bytes=int(current_scan["file_size_bytes"] or 0),
                modified_at=current_scan["file_modified_at"],
                sha256=current_scan["file_sha256"],
            )
            if not current or current_file is None:
                raise NormalSpeechStaleError(
                    "The media file changed during speech analysis."
                )
            if media is not None and (
                int(current_file["id"]) != int(media.file_id)
                or int(current_file["title_id"]) != int(media.title_id)
                or str(current_file["path"]) != str(media.path)
            ):
                raise NormalSpeechStaleError(
                    "The cataloged media binding changed during speech analysis."
                )
        return current_scan

    def _cached_observation(
        self,
        scan: Mapping[str, Any],
        request: SpeechRequest,
        engine_snapshot: _SpeechEngineSnapshot,
    ) -> NormalSpeechObservation | None:
        media = request.media
        window = request.window
        source_signature = request.audio.source_signature
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT id,cache_key,source_signature,file_size_bytes,
                          file_modified_at,text_value,payload_json
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='speech_transcript'
                     AND analyzer_key=? AND analyzer_version=?
                     AND start_ms=? AND end_ms=? AND status='complete'
                     AND source_signature=?
                   ORDER BY id DESC""",
                (
                    int(scan["file_id"]),
                    NORMAL_SPEECH_ARTIFACT_KEY,
                    NORMAL_SPEECH_ARTIFACT_VERSION,
                    int(window.start_ms),
                    int(window.end_ms),
                    str(source_signature),
                ),
            ).fetchall()

        for row in rows:
            if int(row["file_size_bytes"] or 0) != int(
                scan["file_size_bytes"] or 0
            ):
                continue
            if not _same_modified_at(
                row["file_modified_at"],
                scan["file_modified_at"],
            ):
                continue
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            audio_identity = _audio_identity_from_payload(payload)
            transcript = _transcript_from_row(row["text_value"], payload)
            if audio_identity is None or transcript is None:
                continue
            if audio_identity.source_signature != str(source_signature):
                continue

            cached_request = SpeechRequest(
                media=media,
                window=window,
                audio=audio_identity,
                model=self.model,
                language=request.language,
                translate=request.translate,
                parameters=request.parameters,
            )
            try:
                expected_cache_key = speech_transcript_cache_key(
                    cached_request,
                    engine_snapshot,
                )
            except (SpeechIdentityError, TypeError, ValueError):
                continue
            if expected_cache_key != str(row["cache_key"] or ""):
                continue

            artifact_id = int(row["id"])
            return NormalSpeechObservation(
                window=window,
                cache_key=expected_cache_key,
                source_signature=str(source_signature),
                transcript=transcript,
                audio_identity=audio_identity,
                artifact_id=artifact_id,
                reused=True,
            )
        return None

    def _stable_cached_observation(
        self,
        scan: Mapping[str, Any],
        media: MediaIdentityFile,
        request: SpeechRequest,
    ) -> tuple[
        NormalSpeechObservation | None,
        _SpeechEngineSnapshot,
        str,
    ]:
        """Reuse only after current bounded audio bytes and runtime identity agree."""
        first_snapshot = self._engine_snapshot()
        first_cache_key = speech_transcript_cache_key(
            request,
            first_snapshot,
        )
        cached = self._cached_observation(
            scan,
            request,
            first_snapshot,
        )
        if cached is None or cached.cache_key != first_cache_key:
            return None, first_snapshot, first_cache_key

        second_snapshot = self._engine_snapshot()
        second_cache_key = speech_transcript_cache_key(
            request,
            second_snapshot,
        )
        if second_cache_key != first_cache_key:
            return None, second_snapshot, second_cache_key
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET last_used_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (int(cached.artifact_id),),
            )
        return cached, second_snapshot, second_cache_key

    def _persist_observation(
        self,
        scan_id: int,
        baseline_scan: Mapping[str, Any],
        media: MediaIdentityFile,
        extractor: LocalFfmpegSpeechAudioExtractor,
        window: SpeechWindow,
        request: SpeechRequest,
        cache_key: str,
        transcript: SpeechTranscript,
        engine_snapshot: _SpeechEngineSnapshot,
    ) -> NormalSpeechObservation:
        current_scan = self._require_fresh_scan(
            scan_id,
            baseline_scan,
            media,
        )
        binary_identity = engine_snapshot.binary
        payload = {
            "version": 1,
            "window": {
                "start_ms": window.start_ms,
                "end_ms": window.end_ms,
                "purpose": window.purpose,
            },
            "audio_identity": {
                **dict(request.audio.cache_identity()),
                "source_signature": request.audio.source_signature,
                "details": request.audio.details_payload(),
            },
            "stream": dict(extractor.stream.cache_identity()),
            "engine": {
                "key": engine_snapshot.key,
                "version": engine_snapshot.version,
                "binary": {
                    **dict(binary_identity.cache_identity()),
                    "source": binary_identity.source,
                    "license_id": binary_identity.license_id,
                    "details": binary_identity.details_payload(),
                },
                "identity": dict(engine_snapshot.identity),
            },
            "model": {
                **dict(self.model.cache_identity()),
                "source": self.model.source,
                "license_id": self.model.license_id,
                "details": self.model.details_payload(),
            },
            "request": {
                "language": request.language,
                "translate": request.translate,
                "parameters": dict(request.parameters),
            },
            "transcript": {
                "language": transcript.language,
                "confidence": transcript.confidence,
                "details": transcript.details_payload(),
            },
        }
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Revalidate again inside the write transaction so a changed scan
            # cannot slip between the pre-commit freshness check and INSERT.
            row = conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (int(scan_id),),
            ).fetchone()
            if not row:
                raise NormalSpeechStaleError(
                    "Episode Identity scan disappeared before transcript persistence."
                )
            transaction_scan = dict(row)
            if (
                transaction_scan["status"] != "complete"
                or str(transaction_scan["metadata_signature"] or "")
                != str(current_scan["metadata_signature"] or "")
                or int(transaction_scan["file_size_bytes"] or 0)
                != int(current_scan["file_size_bytes"] or 0)
                or not _same_modified_at(
                    transaction_scan["file_modified_at"],
                    current_scan["file_modified_at"],
                )
                or str(transaction_scan["file_sha256"] or "")
                != str(current_scan["file_sha256"] or "")
            ):
                raise NormalSpeechStaleError(
                    "Episode Identity inputs changed before transcript persistence."
                )

            transaction_current, transaction_file = (
                MediaIdentityDecisionService._snapshot_is_current(
                    conn,
                    int(transaction_scan["file_id"]),
                    size_bytes=int(transaction_scan["file_size_bytes"] or 0),
                    modified_at=transaction_scan["file_modified_at"],
                    sha256=transaction_scan["file_sha256"],
                )
            )
            if (
                not transaction_current
                or transaction_file is None
                or int(transaction_file["id"]) != int(media.file_id)
                or int(transaction_file["title_id"]) != int(media.title_id)
                or str(transaction_file["path"]) != str(media.path)
            ):
                raise NormalSpeechStaleError(
                    "The media binding changed before transcript persistence."
                )

            insert_cursor = conn.execute(
                """INSERT OR IGNORE INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     start_ms,end_ms,text_value,payload_json,
                     updated_at,last_used_at
                   ) VALUES (
                     ?,'speech_transcript',?,? ,?,'complete','normal',
                     'local_speech',?,?,?,?,?,?,?,?,
                     CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
                   )""",
                (
                    int(transaction_scan["file_id"]),
                    NORMAL_SPEECH_ARTIFACT_KEY,
                    NORMAL_SPEECH_ARTIFACT_VERSION,
                    cache_key,
                    f"file:{int(transaction_scan['file_id'])}:audio:{extractor.stream.index}",
                    request.audio.source_signature,
                    int(transaction_scan["file_size_bytes"] or 0),
                    transaction_scan["file_modified_at"],
                    int(window.start_ms),
                    int(window.end_ms),
                    transcript.text,
                    _canonical_json(payload),
                ),
            )
            inserted = insert_cursor.rowcount == 1
            persisted = conn.execute(
                """SELECT id,text_value,payload_json,status
                   FROM media_identity_artifacts
                   WHERE file_id=? AND artifact_type='speech_transcript'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?
                   ORDER BY id DESC LIMIT 1""",
                (
                    int(transaction_scan["file_id"]),
                    NORMAL_SPEECH_ARTIFACT_KEY,
                    NORMAL_SPEECH_ARTIFACT_VERSION,
                    cache_key,
                ),
            ).fetchone()
            if not persisted:
                raise NormalSpeechError(
                    "InfoMancer could not persist the speech transcript safely."
                )
            artifact_id = int(persisted["id"])
            persisted_payload: Mapping[str, Any] | None = None
            try:
                loaded_payload = json.loads(
                    str(persisted["payload_json"] or "{}")
                )
                if isinstance(loaded_payload, Mapping):
                    persisted_payload = loaded_payload
            except (TypeError, ValueError, json.JSONDecodeError):
                persisted_payload = None

            persisted_audio = (
                _audio_identity_from_payload(persisted_payload)
                if persisted_payload is not None
                else None
            )
            persisted_transcript = (
                _transcript_from_row(
                    persisted["text_value"],
                    persisted_payload,
                )
                if persisted_payload is not None
                else None
            )
            persisted_matches = False
            if (
                str(persisted["status"] or "") == "complete"
                and persisted_audio is not None
                and persisted_transcript is not None
            ):
                try:
                    persisted_request = SpeechRequest(
                        media=media,
                        window=window,
                        audio=persisted_audio,
                        model=self.model,
                        language=request.language,
                        translate=request.translate,
                        parameters=request.parameters,
                    )
                    persisted_matches = (
                        speech_transcript_cache_key(
                            persisted_request,
                            engine_snapshot,
                        )
                        == cache_key
                    )
                except (SpeechIdentityError, TypeError, ValueError):
                    persisted_matches = False

            if not persisted_matches:
                # The cache row is derived data. If its unique cache key blocks
                # regeneration but its payload is corrupt or identity-mismatched,
                # repair that exact row from the freshly verified transcript.
                repair_cursor = conn.execute(
                    """UPDATE media_identity_artifacts
                       SET status='complete',
                           profile='normal',
                           source_kind='local_speech',
                           source_ref=?,
                           source_signature=?,
                           file_size_bytes=?,
                           file_modified_at=?,
                           start_ms=?,
                           end_ms=?,
                           text_value=?,
                           payload_json=?,
                           error='',
                           updated_at=CURRENT_TIMESTAMP,
                           last_used_at=CURRENT_TIMESTAMP
                       WHERE id=? AND cache_key=?""",
                    (
                        f"file:{int(transaction_scan['file_id'])}:audio:{extractor.stream.index}",
                        request.audio.source_signature,
                        int(transaction_scan["file_size_bytes"] or 0),
                        transaction_scan["file_modified_at"],
                        int(window.start_ms),
                        int(window.end_ms),
                        transcript.text,
                        _canonical_json(payload),
                        artifact_id,
                        cache_key,
                    ),
                )
                if repair_cursor.rowcount != 1:
                    raise NormalSpeechError(
                        "InfoMancer could not repair the speech transcript cache row safely."
                    )
                persisted_audio = request.audio
                persisted_transcript = transcript
                inserted = True
            else:
                conn.execute(
                    """UPDATE media_identity_artifacts
                       SET last_used_at=CURRENT_TIMESTAMP
                       WHERE id=?""",
                    (artifact_id,),
                )

        return NormalSpeechObservation(
            window=window,
            cache_key=cache_key,
            source_signature=persisted_audio.source_signature,
            transcript=persisted_transcript,
            audio_identity=persisted_audio,
            artifact_id=artifact_id,
            reused=not inserted,
        )

    def run(
        self,
        scan_id: int,
        scan: Mapping[str, Any],
        media: MediaIdentityFile,
        runtime_seconds: Any,
        streams: Sequence[Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    ) -> NormalSpeechRun:
        windows = plan_normal_speech_windows(runtime_seconds)
        if not windows:
            return NormalSpeechRun(
                planned_windows=(),
                failures=("speech-runtime-unavailable",),
            )
        try:
            validate_normal_speech_window_plan(windows)
        except SpeechIdentityError as exc:
            return NormalSpeechRun(
                planned_windows=windows,
                failures=(_bounded_failure("speech-window-plan", exc),),
                budget_exhausted=True,
            )

        try:
            if not self.engine.available():
                return NormalSpeechRun(
                    planned_windows=windows,
                    failures=("speech-engine-unavailable",),
                )
        except Exception as exc:
            return NormalSpeechRun(
                planned_windows=windows,
                failures=(_bounded_failure("speech-engine-unavailable", exc),),
            )

        self._require_fresh_scan(int(scan_id), scan, media)
        try:
            extractor = self.extractor_factory(
                media,
                streams,
                preferred_language=self.preferred_audio_language,
            )
            selected_language = normalize_speech_language(
                extractor.stream.language
            )
            request_language = (
                ""
                if selected_language == "und"
                else selected_language
            )
            synopsis_language = self.synopsis_language
            translation_target = self.translation_target_language
            if (
                not translation_target
                and synopsis_language == "eng"
                and selected_language != "eng"
            ):
                translation_target = "eng"
            request_translate = bool(
                self.translate
                or (
                    translation_target == "eng"
                    and selected_language != "eng"
                )
            )
            model_multilingual = bool(
                self.model.details_payload().get("multilingual", False)
            )
            if (
                not model_multilingual
                and (
                    request_translate
                    or selected_language != "eng"
                )
            ):
                return NormalSpeechRun(
                    planned_windows=windows,
                    failures=("speech-language-incompatible-with-model",),
                    synopsis_language=synopsis_language,
                    preferred_audio_language=self.preferred_audio_language,
                    selected_audio_language=selected_language,
                    transcription_input_language=request_language,
                    translation_target_language=(
                        "eng" if request_translate else ""
                    ),
                )
        except SpeechAudioStaleError as exc:
            raise NormalSpeechStaleError(str(exc)) from exc
        except (SpeechAudioUnavailable, SpeechIdentityError, OSError) as exc:
            return NormalSpeechRun(
                planned_windows=windows,
                failures=(_bounded_failure("speech-audio-unavailable", exc),),
            )

        observations: list[NormalSpeechObservation] = []
        failures: list[str] = []
        budget_records: list[tuple[SpeechWindow, SpeechAudioIdentity]] = []

        for window in windows:
            self._require_fresh_scan(int(scan_id), scan, media)

            # Transcript cache reuse is intentionally checked only after
            # extraction. The cache key is bound to the exact bounded WAV
            # bytes, so path/size/mtime provenance alone is never sufficient
            # to reuse a transcript.
            prepared: ExtractedSpeechAudio | None = None
            try:
                prepared = extractor.extract(window)
                request = SpeechRequest(
                    media=media,
                    window=window,
                    audio=prepared.identity,
                    model=self.model,
                    language=request_language,
                    translate=request_translate,
                    parameters=self.parameters,
                )
                exact_cached, engine_snapshot, cache_key = (
                    self._stable_cached_observation(
                        scan,
                        media,
                        request,
                    )
                )
                if exact_cached is not None:
                    validate_normal_speech_audio_budget(
                        [*budget_records, (window, exact_cached.audio_identity)]
                    )
                    self._require_fresh_scan(
                        int(scan_id),
                        scan,
                        media,
                    )
                    budget_records.append(
                        (window, exact_cached.audio_identity)
                    )
                    observations.append(exact_cached)
                    continue

                validate_normal_speech_audio_budget(
                    [*budget_records, (window, prepared.identity)]
                )
                validated_path = prepared.validated_path(request.audio)
                transcript = self.engine.transcribe(
                    validated_path,
                    request,
                )
                if not isinstance(transcript, SpeechTranscript):
                    raise SpeechIdentityError(
                        "Speech engines must return SpeechTranscript values."
                    )
                post_engine_snapshot = self._engine_snapshot()
                if (
                    speech_transcript_cache_key(
                        request,
                        post_engine_snapshot,
                    )
                    != cache_key
                ):
                    raise SpeechIdentityError(
                        "Speech engine identity changed during transcription."
                    )
                self._require_fresh_scan(int(scan_id), scan, media)
                observation = self._persist_observation(
                    int(scan_id),
                    scan,
                    media,
                    extractor,
                    window,
                    request,
                    cache_key,
                    transcript,
                    post_engine_snapshot,
                )
                budget_records.append((window, request.audio))
                observations.append(observation)
            except SpeechAudioStaleError as exc:
                raise NormalSpeechStaleError(str(exc)) from exc
            except NormalSpeechStaleError:
                raise
            except SpeechBudgetExceeded as exc:
                failures.append(
                    _bounded_failure(f"speech:{window.key}", exc)
                )
                return NormalSpeechRun(
                    planned_windows=windows,
                    observations=tuple(observations),
                    failures=tuple(failures),
                    budget_exhausted=True,
                    synopsis_language=synopsis_language,
                    preferred_audio_language=self.preferred_audio_language,
                    selected_audio_language=selected_language,
                    transcription_input_language=request_language,
                    translation_target_language=(
                        "eng" if request_translate else ""
                    ),
                )
            except (SpeechAudioUnavailable, SpeechIdentityError) as exc:
                failures.append(
                    _bounded_failure(f"speech:{window.key}", exc)
                )
            except Exception as exc:
                # Speech is an optional Normal escalation. Runtime/model/backend
                # failures must not discard cheaper evidence or previously
                # completed transcript fragments. KeyboardInterrupt/SystemExit
                # are BaseException subclasses and intentionally propagate so
                # an interrupted run can resume from persisted fragments.
                failures.append(
                    _bounded_failure(f"speech:{window.key}", exc)
                )
            finally:
                if prepared is not None:
                    prepared.cleanup()

        return NormalSpeechRun(
            planned_windows=windows,
            observations=tuple(observations),
            failures=tuple(failures),
            budget_exhausted=False,
            synopsis_language=synopsis_language,
            preferred_audio_language=self.preferred_audio_language,
            selected_audio_language=selected_language,
            transcription_input_language=request_language,
            translation_target_language=(
                "eng" if request_translate else ""
            ),
        )
