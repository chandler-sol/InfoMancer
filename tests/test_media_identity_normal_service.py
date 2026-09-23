from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.mie import MediaIntelligenceEngine
from app.media_identity.external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalSourceRegistry,
    ExternalSourceStatus,
    PreviewFrameRef,
)
from app.media_identity.fast import FastIdentityService
from app.media_identity.decision_snapshot import (
    result_revision,
    seal_decision_snapshot,
)
from app.media_identity.normal import (
    NormalIdentityError,
    NormalResourceLimits,
    OcrTextResult,
    ocr_preview_cache_key,
)
from app.media_identity.local_frames import LOCAL_FRAME_SOURCE_KEY
from app.media_identity.normal_service import (
    NORMAL_OCR_ARTIFACT_KEY,
    NORMAL_OCR_ARTIFACT_VERSION,
    NORMAL_OCR_EVIDENCE_KEY,
    NORMAL_SPEECH_EVIDENCE_KEY,
    NormalIdentityScanError,
    NormalIdentityService,
)
from app.media_identity.service import MediaIdentityDecisionService
from app.media_identity.speech import (
    SpeechAudioIdentity,
    SpeechBinaryIdentity,
    SpeechModelIdentity,
    SpeechTranscript,
    SpeechWindow,
)
from app.media_identity.speech_audio import SpeechAudioStream
from app.media_identity.speech_service import (
    NormalSpeechObservation,
    NormalSpeechRun,
)
from app.media_identity.versions import (
    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION,
    NORMAL_EVIDENCE_ALGORITHM_VERSION,
)


class FakeOcr:
    key = "fake-ocr"
    version = "1"

    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def cache_identity(self):
        return {"fixture": "fake-ocr-v1"}

    def recognize(self, image: bytes) -> OcrTextResult:
        self.calls += 1
        return OcrTextResult(
            text=image.decode("utf-8"),
            confidence=1.0,
            details={"fixture": True},
        )


class FakeNormalSpeechPrepared:
    def __init__(self, identity):
        self.identity = identity
        self.cleanup_calls = 0

    def validated_path(self, expected_identity=None):
        if expected_identity is not None and expected_identity != self.identity:
            raise RuntimeError("speech identity mismatch")
        return "/tmp/fake-normal-speech.wav"

    def cleanup(self):
        self.cleanup_calls += 1


class FakeNormalSpeechExtractor:
    instances = []

    def __init__(self, media, streams, *, preferred_language=""):
        self.media = media
        self.streams = tuple(streams)
        self.preferred_language = preferred_language
        self.stream = SpeechAudioStream(
            index=0,
            language="eng",
            channels=2,
            sample_rate_hz=48_000,
            default=True,
        )
        self.extract_calls = 0
        self.prepared = []
        type(self).instances.append(self)

    def source_signature(self, window):
        return hashlib.sha256(
            f"normal:{self.media.file_id}:{window.start_ms}:{window.end_ms}".encode()
        ).hexdigest()

    def extract(self, window):
        self.extract_calls += 1
        payload = f"normal-audio:{window.start_ms}:{window.end_ms}".encode()
        identity = SpeechAudioIdentity(
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            format_key="wav-pcm-s16le",
            sample_rate_hz=16_000,
            channels=1,
            source_signature=self.source_signature(window),
        )
        prepared = FakeNormalSpeechPrepared(identity)
        self.prepared.append(prepared)
        return prepared


class FakeNormalSpeechEngine:
    key = "fake-normal-speech"
    version = "1"

    def __init__(self, *, available=True):
        self.is_available = available
        self.calls = 0

    def available(self):
        return self.is_available

    def binary_identity(self):
        return SpeechBinaryIdentity(
            key=self.key,
            version=self.version,
            sha256="d" * 64,
            size_bytes=2048,
            source="fixture",
            details={"runtime_tree_sha256": "e" * 64},
        )

    def cache_identity(self):
        return {"fixture": "normal-speech-v1", "cpu_only": True}

    def transcribe(self, _audio_path, request):
        self.calls += 1
        return SpeechTranscript(
            text=f"dialogue {request.window.start_ms}-{request.window.end_ms}",
            language="en",
        )


def fake_normal_speech_model():
    return SpeechModelIdentity(
        key="base-q5_1",
        version="fixture",
        sha256="f" * 64,
        size_bytes=4096,
        source="fixture",
        details={"multilingual": True},
    )


class FakePreviewSource:
    source_key = "jellyfin"
    version = "1"

    def __init__(self, *, mutate=None):
        self.read_calls = 0
        self.mutate = mutate

    def status(self):
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=True,
            capabilities=frozenset({ExternalCapability.PREVIEW_FRAMES}),
        )

    def resolve_media(self, _context):
        return ExternalMediaRef(
            source_key=self.source_key,
            item_id="episode-1",
            path="/srv/tv/Example Show/S01E01.mkv",
            source_signature="media-v1",
        )

    def preview_frames(self, _media):
        return (
            PreviewFrameRef(
                source_key=self.source_key,
                item_id="episode-1",
                timestamp_ms=10_000,
                asset_ref="tile:1",
                source_signature="preview-v1",
                width=320,
                height=180,
            ),
        )

    def read_preview(self, _frame):
        self.read_calls += 1
        if self.mutate is not None:
            self.mutate()
        return b"bronze harbor lantern meadow quartz thunder"

    def subtitles(self, _media):
        return ()

    def read_subtitle(self, _subtitle):
        return b""

    def media_metadata(self, _media):
        return {}

    def fingerprints(self, _media):
        return ()

    def known_identity(self, _media):
        return None


class NormalIdentityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example Show"
        self.show_root.mkdir(parents=True)

        self.media_path = self.show_root / "Example Show - S01E01.mkv"
        self.media_path.write_bytes(b"fixture-media" * 32)
        stat = self.media_path.stat()

        self.database = Database(self.root / "catalog.db")
        self.database.initialize()

        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,folder_path,tvdb_id
                   ) VALUES (1,1,'tv','Example Show','Example Show',?,4242)""",
                (str(self.show_root),),
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,runtime_seconds,
                     width,height,video_codec,audio_codec,audio_channels,bitrate,
                     container,dynamic_range,media_info_at,media_info_error,seen_scan
                   ) VALUES (
                     1,1,?,?,?,?,?,1,1,1,'Example Show',1440,1920,1080,
                     'H264','AAC',2,5000000,'MKV','SDR',
                     '2026-09-21T12:00:00','','fixture-scan'
                   )""",
                (
                    str(self.media_path),
                    self.media_path.name,
                    "mkv",
                    self.media_path.stat().st_size,
                    stat.st_mtime,
                ),
            )
            conn.execute(
                """INSERT INTO provider_episode_series_cache(
                     provider,provider_series_id,language,source_signature,
                     episode_count,mapping_count,order_namespaces_json
                   ) VALUES (
                     'tvdb','4242','eng','provider-v1',2,2,
                     '[{"namespace":"default"}]'
                   )"""
            )
            identities = [
                (
                    "tvdb","4242","1001","eng","Episode One",
                    "amber falcon orchard glacier velvet compass",
                    "2026-01-01",json.dumps({"runtime":24}),
                ),
                (
                    "tvdb","4242","1002","eng","Episode Two",
                    "bronze harbor lantern meadow quartz thunder",
                    "2026-01-08",json.dumps({"runtime":24}),
                ),
            ]
            conn.executemany(
                """INSERT INTO provider_episode_identities(
                     provider,provider_series_id,provider_episode_id,language,
                     name,overview,aired,metadata_json
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                identities,
            )
            mappings = [
                ("tvdb","4242","1001","eng","default","Default",1,1,1,json.dumps([1,1,1]),"{}"),
                ("tvdb","4242","1002","eng","default","Default",1,2,2,json.dumps([1,2,2]),"{}"),
            ]
            conn.executemany(
                """INSERT INTO provider_episode_mappings(
                     provider,provider_series_id,provider_episode_id,language,
                     order_namespace,order_name,season,episode,absolute_number,
                     coordinate_key,details_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                mappings,
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,?,?,?,?,?)""",
                [
                    (1,1,1001,1,1,"Episode One"),
                    (2,1,1002,1,2,"Episode Two"),
                ],
            )

        self.fast = FastIdentityService(self.database)
        self.fast_scan = self.fast.scan_file(1)

    def tearDown(self):
        self.temporary.cleanup()

    def _reseal_scan_fixture(self) -> None:
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            seal_decision_snapshot(
                conn,
                self.fast_scan.scan_id,
                revision=result_revision(
                    {"claimed_identity_json": row["claimed_identity_json"]}
                ) + 1,
            )

    def test_decision_version_drift_makes_fast_scan_stale(self):
        decisions = MediaIdentityDecisionService(self.database)
        detail = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertTrue(detail["snapshot_current"])

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            self.assertEqual(
                claimed["decision_algorithm_version"],
                EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION,
            )
            claimed["decision_algorithm_version"] += 1
            conn.execute(
                "UPDATE media_identity_scans SET claimed_identity_json=? WHERE id=?",
                (json.dumps(claimed, sort_keys=True), self.fast_scan.scan_id),
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_normal_version_drift_makes_completed_scan_stale(self):
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource()]),
            FakeOcr(),
        )
        service.run_scan(self.fast_scan.scan_id)
        decisions = MediaIdentityDecisionService(self.database)
        detail = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertTrue(detail["snapshot_current"])

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            self.assertEqual(
                claimed["normal_ocr"]["algorithm_version"],
                NORMAL_EVIDENCE_ALGORITHM_VERSION,
            )
            claimed["normal_ocr"]["algorithm_version"] += 1
            conn.execute(
                "UPDATE media_identity_scans SET claimed_identity_json=? WHERE id=?",
                (json.dumps(claimed, sort_keys=True), self.fast_scan.scan_id),
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def _seed_jellyfin_config(self, *, revision=1, server_url="https://jf-one.local"):
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO external_analysis_sources(
                     source_key,enabled,server_url,metadata_root,config_json,
                     credential_generation,config_revision,updated_at
                   ) VALUES ('jellyfin',1,?,'','{}','generation-a',?,CURRENT_TIMESTAMP)
                   ON CONFLICT(source_key) DO UPDATE SET
                     enabled=1,server_url=excluded.server_url,
                     config_json='{}',credential_generation='generation-a',
                     config_revision=excluded.config_revision,
                     updated_at=CURRENT_TIMESTAMP""",
                (server_url, int(revision)),
            )

    def test_actionable_external_ocr_revalidates_current_preview_bytes(self):
        class MutablePreview(FakePreviewSource):
            def __init__(self):
                super().__init__()
                self.payload = b"bronze harbor lantern meadow quartz thunder"

            def read_preview(self, _frame):
                self.read_calls += 1
                return self.payload

        self._seed_jellyfin_config()
        source = MutablePreview()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            FakeOcr(),
        )
        service.run_scan(self.fast_scan.scan_id)

        decisions = MediaIdentityDecisionService(
            self.database,
            external_registry_factory=lambda: ExternalSourceRegistry([source]),
        )
        resolution = decisions.resolve_scan(self.fast_scan.scan_id)
        self.assertEqual(resolution.state.value, "strong_match_other")
        before = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertTrue(before["snapshot_current"])
        self.assertTrue(before["actionable"])

        source.payload = b"amber falcon orchard glacier velvet compass"
        after = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(after["snapshot_current"])
        self.assertFalse(after["actionable"])
        self.assertEqual(
            decisions.rename_preview(self.fast_scan.scan_id)["status"],
            "stale",
        )
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            decisions.confirm_best(self.fast_scan.scan_id, None)

    def test_mie_uses_external_preview_freshness_boundary(self):
        class MutablePreview(FakePreviewSource):
            def __init__(self):
                super().__init__()
                self.payload = b"bronze harbor lantern meadow quartz thunder"

            def read_preview(self, _frame):
                self.read_calls += 1
                return self.payload

        self._seed_jellyfin_config()
        source = MutablePreview()
        registry_factory = lambda: ExternalSourceRegistry([source])
        normal = NormalIdentityService(
            self.database,
            registry_factory(),
            FakeOcr(),
        )
        normal.run_scan(self.fast_scan.scan_id)

        decisions = MediaIdentityDecisionService(
            self.database,
            external_registry_factory=registry_factory,
        )
        resolution = decisions.resolve_scan(self.fast_scan.scan_id)
        self.assertEqual(resolution.state.value, "strong_match_other")

        mie = MediaIntelligenceEngine(
            self.database,
            external_registry_factory=registry_factory,
        )
        before = mie.identity_decisions.mie_findings()
        self.assertEqual(len(before), 1)
        self.assertEqual(
            before[0]["evidence"]["scan_id"],
            self.fast_scan.scan_id,
        )

        source.payload = b"amber falcon orchard glacier velvet compass"
        self.assertEqual(mie.identity_decisions.mie_findings(), [])

    def test_new_fast_scan_does_not_hide_current_normal_mie_result(self):
        source = FakePreviewSource()
        normal = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            FakeOcr(),
        )
        normal.run_scan(self.fast_scan.scan_id)

        decisions = MediaIdentityDecisionService(self.database)
        old_resolution = decisions.resolve_scan(self.fast_scan.scan_id)
        self.assertEqual(old_resolution.state.value, "strong_match_other")
        old_detail = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertEqual(old_detail["completed_profile"], "normal")
        self.assertTrue(old_detail["snapshot_current"])
        self.assertTrue(old_detail["actionable"])

        newer_fast = self.fast.scan_file(1)
        self.assertGreater(newer_fast.scan_id, self.fast_scan.scan_id)
        newer_resolution = decisions.resolve_scan(newer_fast.scan_id)
        newer_detail = decisions.scan_detail(newer_fast.scan_id)
        self.assertEqual(newer_detail["completed_profile"], "fast")
        self.assertTrue(newer_detail["snapshot_current"])
        self.assertFalse(newer_detail["actionable"])
        self.assertNotEqual(
            newer_resolution.state.value,
            "strong_match_other",
        )

        latest = decisions.latest_scan_for_file(1)
        self.assertEqual(latest["id"], newer_fast.scan_id)

        findings = decisions.mie_findings()
        self.assertEqual(len(findings), 1)
        self.assertEqual(
            findings[0]["evidence"]["scan_id"],
            self.fast_scan.scan_id,
        )
        self.assertEqual(
            findings[0]["evidence"]["result_state"],
            "strong_match_other",
        )

    def test_external_source_config_change_stales_normal_result(self):
        self._seed_jellyfin_config()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource()]),
            FakeOcr(),
        )
        service.run_scan(self.fast_scan.scan_id)
        decisions = MediaIdentityDecisionService(self.database)
        decisions.resolve_scan(self.fast_scan.scan_id)
        self.assertTrue(
            decisions.scan_detail(self.fast_scan.scan_id)["snapshot_current"]
        )

        with self.database.connect() as conn:
            conn.execute(
                """UPDATE external_analysis_sources
                   SET server_url='https://jf-two.local',
                       config_revision=config_revision+1
                   WHERE source_key='jellyfin'"""
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_external_source_config_change_during_ocr_blocks_publication(self):
        self._seed_jellyfin_config()

        def mutate_config():
            with self.database.connect() as conn:
                conn.execute(
                    """UPDATE external_analysis_sources
                       SET server_url='https://jf-two.local',
                           config_revision=config_revision+1
                       WHERE source_key='jellyfin'"""
                )

        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource(mutate=mutate_config)]),
            FakeOcr(),
        )
        with self.assertRaisesRegex(
            NormalIdentityScanError,
            "configuration changed during Normal OCR",
        ):
            service.run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT completed_profile,stage
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
        self.assertEqual(scan["completed_profile"], "fast")
        self.assertEqual(scan["stage"], "fast_complete")

    def test_normal_speech_orchestration_version_drift_makes_scan_stale(self):
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource()]),
            FakeOcr(),
        )
        service.run_scan(self.fast_scan.scan_id)
        decisions = MediaIdentityDecisionService(self.database)
        self.assertTrue(
            decisions.scan_detail(self.fast_scan.scan_id)["snapshot_current"]
        )

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            claimed["normal_speech"]["algorithm_version"] += 1
            conn.execute(
                "UPDATE media_identity_scans SET claimed_identity_json=? WHERE id=?",
                (
                    json.dumps(claimed, sort_keys=True),
                    self.fast_scan.scan_id,
                ),
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_normal_ocr_persists_visual_text_artifact_and_candidate_evidence(self):
        source = FakePreviewSource()
        engine = FakeOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        )

        result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.observation_count, 1)
        self.assertEqual(result.text_observation_count, 1)
        self.assertEqual(result.reused_artifact_count, 0)
        self.assertEqual(source.read_calls, 1)
        self.assertEqual(engine.calls, 1)

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT requested_profile,completed_profile,stage,result_state
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            artifacts = conn.execute(
                """SELECT artifact_type,source_kind,source_signature,text_value
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'"""
            ).fetchall()
            evidence = conn.execute(
                """SELECT candidate_key,evidence_category,relation,strength
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'
                   ORDER BY candidate_key""",
                (self.fast_scan.scan_id,),
            ).fetchall()

        self.assertEqual(scan["requested_profile"], "normal")
        self.assertEqual(scan["completed_profile"], "normal")
        self.assertEqual(scan["stage"], "normal_ocr_complete")
        self.assertIsNone(scan["result_state"])
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["source_kind"], "jellyfin")
        self.assertEqual(artifacts[0]["source_signature"], "preview-v1")
        self.assertIn("bronze harbor", artifacts[0]["text_value"])
        self.assertTrue(
            any(
                row["candidate_key"].endswith('"1002"]')
                and row["evidence_category"] == "visual_text"
                and row["relation"] == "supports"
                and float(row["strength"]) > 0.9
                for row in evidence
            )
        )

    def test_ocr_insert_race_scores_persisted_winner_not_losing_output(self):
        source = FakePreviewSource()
        engine = FakeOcr()
        frame = source.preview_frames(None)[0]
        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT file_size_bytes,file_modified_at,file_sha256
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            cache_key = ocr_preview_cache_key(
                frame,
                engine,
                parameters={
                    "file_id": 1,
                    "size_bytes": int(scan["file_size_bytes"]),
                    "modified_at": scan["file_modified_at"],
                    "sha256": str(scan["file_sha256"] or ""),
                },
                preview_sha256=hashlib.sha256(
                    b"bronze harbor lantern meadow quartz thunder"
                ).hexdigest(),
            )
            winner_text = "amber falcon orchard glacier velvet compass"
            conn.execute(
                """INSERT INTO media_identity_artifacts(
                     file_id,artifact_type,analyzer_key,analyzer_version,
                     cache_key,status,profile,source_kind,source_ref,
                     source_signature,file_size_bytes,file_modified_at,
                     start_ms,end_ms,text_value,payload_json
                   ) VALUES (
                     1,'visual_text',?,?,?,'complete','normal',?,?,?,
                     ?,?,?,?,?,?
                   )""",
                (
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    cache_key,
                    frame.source_key,
                    frame.asset_ref,
                    frame.source_signature,
                    int(scan["file_size_bytes"]),
                    scan["file_modified_at"],
                    frame.timestamp_ms,
                    frame.timestamp_ms,
                    winner_text,
                    json.dumps({
                        "confidence": 1.0,
                        "stage": 1,
                        "ordinal": 1,
                        "image_bytes": 64,
                        "reused": False,
                        "item_id": frame.item_id,
                        "engine_key": engine.key,
                        "engine_version": engine.version,
                        "details": {"winner": True},
                    }, sort_keys=True),
                ),
            )

        class ForcedMissService(NormalIdentityService):
            def _cached_ocr(self, scan, frame, cache_key):
                return None

        result = ForcedMissService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.observation_count, 1)
        self.assertEqual(result.reused_artifact_count, 1)
        self.assertEqual(engine.calls, 1)

        with self.database.connect() as conn:
            artifact = conn.execute(
                """SELECT id,text_value FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'
                     AND analyzer_key=? AND analyzer_version=? AND cache_key=?""",
                (
                    NORMAL_OCR_ARTIFACT_KEY,
                    NORMAL_OCR_ARTIFACT_VERSION,
                    cache_key,
                ),
            ).fetchone()
            evidence = conn.execute(
                """SELECT candidate_key,relation,strength,details_json
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?
                   ORDER BY candidate_key""",
                (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
            ).fetchall()

        self.assertEqual(artifact["text_value"], winner_text)
        supported = [row for row in evidence if row["relation"] == "supports"]
        self.assertTrue(
            any(row["candidate_key"].endswith('"1001"]') for row in supported)
        )
        self.assertFalse(
            any(row["candidate_key"].endswith('"1002"]') for row in supported)
        )
        excerpts = [
            json.loads(row["details_json"]).get("text_excerpt", "")
            for row in evidence
        ]
        self.assertTrue(any("amber falcon" in excerpt for excerpt in excerpts))
        self.assertFalse(any("bronze harbor" in excerpt for excerpt in excerpts))

    def test_strong_visual_separation_stops_normal_after_initial_five_frames(self):
        class ManyFrameSource(FakePreviewSource):
            def preview_frames(self, _media):
                return tuple(
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="episode-1",
                        timestamp_ms=index * 10_000,
                        asset_ref=f"tile:{index}",
                        source_signature="preview-many-v1",
                        width=320,
                        height=180,
                    )
                    for index in range(40)
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor lantern meadow quartz thunder"

        source = ManyFrameSource()
        engine = FakeOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        )

        result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.observation_count, 5)
        self.assertEqual(result.text_observation_count, 5)
        self.assertEqual(source.read_calls, 5)
        self.assertEqual(engine.calls, 5)
        with self.database.connect() as conn:
            claimed = json.loads(
                conn.execute(
                    """SELECT claimed_identity_json
                       FROM media_identity_scans WHERE id=?""",
                    (self.fast_scan.scan_id,),
                ).fetchone()["claimed_identity_json"]
            )
        self.assertEqual(
            claimed["normal_ocr"]["highest_observed_stage"],
            1,
        )

    def test_uncalibrated_visual_text_does_not_stop_normal_early(self):
        class ManyFrameSource(FakePreviewSource):
            def preview_frames(self, _media):
                return tuple(
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="episode-1",
                        timestamp_ms=index * 10_000,
                        asset_ref=f"tile:{index}",
                        source_signature="preview-uncalibrated-v1",
                        width=320,
                        height=180,
                    )
                    for index in range(40)
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor lantern meadow quartz thunder"

        class UncalibratedOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                return OcrTextResult(
                    text=image.decode("utf-8"),
                    confidence=None,
                )

        source = ManyFrameSource()
        engine = UncalibratedOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        )

        result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.observation_count, 12)
        self.assertEqual(source.read_calls, 12)
        self.assertEqual(engine.calls, 12)
        self.assertEqual(
            result.highest_observed_stage.name,
            "FINAL",
        )
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT strength,details_json
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'
                     AND candidate_key LIKE '%"1002"]'
                   ORDER BY id LIMIT 1""",
                (self.fast_scan.scan_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertLessEqual(float(row["strength"]), 0.35)
        details = json.loads(row["details_json"])
        self.assertEqual(details["calibrated_observations"], 0)
        self.assertEqual(details["uncalibrated_observations"], 12)

    def test_mixed_calibrated_and_uncalibrated_text_does_not_stop_early(self):
        class ManyFrameSource(FakePreviewSource):
            def preview_frames(self, _media):
                return tuple(
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="episode-1",
                        timestamp_ms=index * 10_000,
                        asset_ref=f"mixed:{index}",
                        source_signature="preview-mixed-confidence-v1",
                        width=320,
                        height=180,
                    )
                    for index in range(40)
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor lantern meadow quartz thunder"

        class MixedConfidenceOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                return OcrTextResult(
                    text=image.decode("utf-8"),
                    confidence=(1.0 if self.calls % 2 else None),
                )

        source = ManyFrameSource()
        engine = MixedConfidenceOcr()
        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        ).run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.observation_count, 12)
        self.assertEqual(result.highest_observed_stage.name, "FINAL")
        self.assertEqual(engine.calls, 12)

    def test_second_normal_run_revalidates_preview_bytes_before_reusing_ocr(self):
        source = FakePreviewSource()
        engine = FakeOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        )
        first = service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.reused_artifact_count, 0)
        self.assertEqual(source.read_calls, 1)
        self.assertEqual(engine.calls, 1)

        second = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(second.reused_artifact_count, 1)
        self.assertEqual(second.visual_frame_attempt_count, 1)
        self.assertEqual(source.read_calls, 2)
        self.assertEqual(engine.calls, 1)
        with self.database.connect() as conn:
            artifact_count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'"""
            ).fetchone()["count"]
        self.assertEqual(int(artifact_count), 1)

    def test_changed_preview_bytes_with_same_reference_do_not_reuse_old_ocr(self):
        class MutablePreview(FakePreviewSource):
            def __init__(self):
                super().__init__()
                self.payload = b"bronze harbor lantern meadow quartz thunder"

            def read_preview(self, _frame):
                self.read_calls += 1
                return self.payload

        source = MutablePreview()
        engine = FakeOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
        )
        first = service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.reused_artifact_count, 0)
        self.assertEqual(engine.calls, 1)

        source.payload = b"amber falcon orchard glacier velvet compass"
        second = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(second.reused_artifact_count, 0)
        self.assertEqual(source.read_calls, 2)
        self.assertEqual(engine.calls, 2)
        with self.database.connect() as conn:
            artifacts = conn.execute(
                """SELECT cache_key,text_value FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'
                   ORDER BY id"""
            ).fetchall()
        self.assertEqual(len(artifacts), 2)
        self.assertNotEqual(artifacts[0]["cache_key"], artifacts[1]["cache_key"])
        self.assertIn("amber falcon", artifacts[1]["text_value"])

    def test_failed_rerun_preserves_existing_completed_normal_evidence(self):
        source = FakePreviewSource()
        first_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            FakeOcr(),
        )
        first = first_service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.completed_profile.value, "normal")

        with self.database.connect() as conn:
            before = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'""",
                (self.fast_scan.scan_id,),
            ).fetchone()["count"]

        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        retry = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "existing completed Normal evidence was retained",
        ):
            retry.run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT completed_profile,stage
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            after = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'""",
                (self.fast_scan.scan_id,),
            ).fetchone()["count"]

        self.assertEqual(scan["completed_profile"], "normal")
        self.assertEqual(scan["stage"], "normal_ocr_complete")
        self.assertEqual(int(after), int(before))
        self.assertGreater(int(after), 0)

    def test_local_ffmpeg_is_used_only_when_external_previews_are_unusable(self):
        class EmptyPreviewSource(FakePreviewSource):
            def preview_frames(self, _media):
                return ()

        class FakeLocalSource(FakePreviewSource):
            source_key = LOCAL_FRAME_SOURCE_KEY

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id="file:1",
                    path=str(self_path),
                    source_signature="local-preview-v1",
                )

            def preview_frames(self, _media):
                return (
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="file:1",
                        timestamp_ms=20_000,
                        asset_ref="ffmpeg:1:20000",
                        source_signature="local-preview-v1",
                        width=640,
                        height=360,
                    ),
                )

        self_path = self.media_path
        external = EmptyPreviewSource()
        local = FakeLocalSource()
        engine = FakeOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            engine,
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource",
            return_value=local,
        ) as local_factory:
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.source_key, LOCAL_FRAME_SOURCE_KEY)
        self.assertEqual(result.observation_count, 1)
        self.assertEqual(external.read_calls, 0)
        self.assertEqual(local.read_calls, 1)
        local_factory.assert_called_once()

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT source_kind FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'
                     AND relation='supports'
                   ORDER BY id LIMIT 1""",
                (self.fast_scan.scan_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_kind"], "generated_preview_ocr")

    def test_textless_external_ocr_falls_back_to_local_generated_frames(self):
        class BlankExternal(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"blank"

        class ConditionalOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                if image == b"blank":
                    return OcrTextResult(text="", confidence=0.95)
                return OcrTextResult(
                    text=image.decode("utf-8"),
                    confidence=0.95,
                )

        class FakeLocalSource(FakePreviewSource):
            source_key = LOCAL_FRAME_SOURCE_KEY

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id="file:1",
                    path=str(self_path),
                    source_signature="local-preview-v2",
                )

            def preview_frames(self, _media):
                return (
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="file:1",
                        timestamp_ms=30_000,
                        asset_ref="ffmpeg:1:30000",
                        source_signature="local-preview-v2",
                        width=640,
                        height=360,
                    ),
                )

        self_path = self.media_path
        external = BlankExternal()
        local = FakeLocalSource()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            ConditionalOcr(),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource",
            return_value=local,
        ):
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.source_key, LOCAL_FRAME_SOURCE_KEY)
        self.assertEqual(result.text_observation_count, 1)
        self.assertEqual(external.read_calls, 1)
        self.assertEqual(local.read_calls, 1)
        self.assertTrue(
            any("jellyfin:ocr:no-visual-text" in item for item in result.failures)
        )

    def test_truncated_ocr_is_not_persisted_as_complete_cache(self):
        class LongOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                return OcrTextResult(
                    text="bronze harbor lantern meadow quartz thunder",
                    confidence=1.0,
                    details={"fixture": True},
                )

        source = FakePreviewSource()
        engine = LongOcr()
        limited = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_ocr_text_chars=8,
            ),
        )
        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource"
        ) as local_factory:
            first = limited.run_scan(self.fast_scan.scan_id)

        self.assertTrue(first.budget_exhausted)
        self.assertEqual(first.observation_count, 0)
        self.assertEqual(engine.calls, 1)
        local_factory.assert_not_called()
        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1
                     AND artifact_type='visual_text'
                     AND analyzer_key=?""",
                (NORMAL_OCR_ARTIFACT_KEY,),
            ).fetchone()["count"]
        self.assertEqual(int(count), 0)

        retry = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_ocr_text_chars=256,
            ),
        )
        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource"
        ) as local_factory:
            second = retry.run_scan(self.fast_scan.scan_id)

        self.assertEqual(second.observation_count, 1)
        self.assertEqual(second.reused_artifact_count, 0)
        self.assertEqual(engine.calls, 2)
        local_factory.assert_not_called()

    def test_weak_jellyfin_ocr_can_yield_to_stronger_plex_before_ffmpeg(self):
        class WeakJellyfin(FakePreviewSource):
            source_key = "jellyfin"

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"unrelated sponsor graphic weather logo"

        class StrongPlex(FakePreviewSource):
            source_key = "plex"

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor lantern meadow quartz thunder"

        jellyfin = WeakJellyfin()
        plex = StrongPlex()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([plex, jellyfin]),
            FakeOcr(),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource"
        ) as local_factory:
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.source_key, "plex")
        self.assertEqual(jellyfin.read_calls, 1)
        self.assertEqual(plex.read_calls, 1)
        self.assertTrue(
            any(
                "jellyfin:ocr:weak-visual-signal" in item
                for item in result.failures
            )
        )
        local_factory.assert_not_called()

    def test_global_frame_budget_prevents_extra_local_fallback_read(self):
        class WeakExternal(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"unrelated sponsor graphic weather logo"

        class StrongLocal(FakePreviewSource):
            source_key = LOCAL_FRAME_SOURCE_KEY

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id="file:1",
                    path=str(self_path),
                    source_signature="local-budget-v1",
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor lantern meadow quartz thunder"

        self_path = self.media_path
        external = WeakExternal()
        local = StrongLocal()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            FakeOcr(),
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
            ),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource",
            return_value=local,
        ):
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(external.read_calls, 1)
        self.assertEqual(local.read_calls, 0)
        self.assertEqual(result.visual_frame_attempt_count, 1)
        self.assertTrue(result.budget_exhausted)

    def test_weak_external_ocr_falls_back_to_stronger_local_frames(self):
        class WeakExternal(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"unrelated sponsor graphic weather logo"

        class StrongLocal(FakePreviewSource):
            source_key = LOCAL_FRAME_SOURCE_KEY

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id="file:1",
                    path=str(self_path),
                    source_signature="local-strong-v1",
                )

            def preview_frames(self, _media):
                return (
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="file:1",
                        timestamp_ms=45_000,
                        asset_ref="ffmpeg:1:45000",
                        source_signature="local-strong-v1",
                        width=1280,
                        height=720,
                    ),
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor lantern meadow quartz thunder"

        self_path = self.media_path
        external = WeakExternal()
        local = StrongLocal()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            FakeOcr(),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource",
            return_value=local,
        ):
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.source_key, LOCAL_FRAME_SOURCE_KEY)
        self.assertEqual(local.read_calls, 1)
        with self.database.connect() as conn:
            supporting = conn.execute(
                """SELECT candidate_key,strength,source_kind
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'
                     AND relation='supports'
                   ORDER BY strength DESC LIMIT 1""",
                (self.fast_scan.scan_id,),
            ).fetchone()
        self.assertIsNotNone(supporting)
        self.assertTrue(supporting["candidate_key"].endswith('"1002"]'))
        self.assertEqual(supporting["source_kind"], "generated_preview_ocr")

    def test_weak_local_fallback_does_not_replace_stronger_external_signal(self):
        class AmbiguousExternal(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"bronze harbor"

        class WorseLocal(FakePreviewSource):
            source_key = LOCAL_FRAME_SOURCE_KEY

            def resolve_media(self, _context):
                return ExternalMediaRef(
                    source_key=self.source_key,
                    item_id="file:1",
                    path=str(self_path),
                    source_signature="local-weak-v1",
                )

            def preview_frames(self, _media):
                return (
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="file:1",
                        timestamp_ms=45_000,
                        asset_ref="ffmpeg:1:45000",
                        source_signature="local-weak-v1",
                        width=1280,
                        height=720,
                    ),
                )

            def read_preview(self, _frame):
                self.read_calls += 1
                return b"unrelated sponsor graphic weather logo"

        self_path = self.media_path
        external = AmbiguousExternal()
        local = WorseLocal()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            FakeOcr(),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource",
            return_value=local,
        ):
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.source_key, "jellyfin")
        self.assertEqual(local.read_calls, 1)

    def test_exhausted_external_budget_prevents_local_fallback(self):
        class BlankExternal(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"1234567890"

        class BlankOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                return OcrTextResult(text="", confidence=0.95)

        external = BlankExternal()
        engine = BlankOcr()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            engine,
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_preview_bytes_per_frame=10,
                max_preview_bytes_total=10,
            ),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource"
        ) as local_factory:
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertTrue(result.budget_exhausted)
        self.assertEqual(result.observation_count, 1)
        self.assertEqual(external.read_calls, 1)
        self.assertEqual(engine.calls, 1)
        local_factory.assert_not_called()

    def test_external_byte_budget_blocks_local_ffmpeg_fallback(self):
        class BlankExternal(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"blank"

        class BlankOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                return OcrTextResult(text="", confidence=0.95)

        external = BlankExternal()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([external]),
            BlankOcr(),
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_preview_bytes_per_frame=5,
                max_preview_bytes_total=5,
            ),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource"
        ) as local_factory:
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertTrue(result.budget_exhausted)
        self.assertEqual(external.read_calls, 1)
        local_factory.assert_not_called()

    def test_local_ffmpeg_is_not_attempted_when_ocr_engine_is_unavailable(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
        )

        with patch(
            "app.media_identity.normal_service.LocalFfmpegFrameSource"
        ) as local_factory:
            result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.completed_profile.value, "fast")
        self.assertIn("ocr-engine-unavailable", result.failures)
        local_factory.assert_not_called()

    def test_failed_normal_fallback_does_not_leave_speech_evidence_on_fast_scan(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(available=False),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.completed_profile.value, "fast")
        self.assertTrue(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 0)
        with self.database.connect() as conn:
            speech_evidence_count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?""",
                (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
            ).fetchone()["count"]
        self.assertEqual(int(speech_evidence_count), 0)

        detail = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertTrue(detail["snapshot_current"])

    def test_weak_normal_evidence_escalates_to_neutral_speech_evidence(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        FakeNormalSpeechExtractor.instances.clear()
        speech_engine = FakeNormalSpeechEngine()
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=speech_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        )

        result = service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.completed_profile.value, "normal")
        self.assertTrue(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 8)
        self.assertEqual(result.speech_reused_artifact_count, 0)
        self.assertEqual(speech_engine.calls, 8)
        self.assertEqual(FakeNormalSpeechExtractor.instances[-1].extract_calls, 8)
        self.assertTrue(
            all(
                item.cleanup_calls == 1
                for item in FakeNormalSpeechExtractor.instances[-1].prepared
            )
        )

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT completed_profile,stage,claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            transcript_count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]
            speech_evidence = conn.execute(
                """SELECT relation,strength,correlation_group
                   FROM media_identity_evidence
                   WHERE scan_id=? AND evidence_category='speech'
                   ORDER BY candidate_key""",
                (self.fast_scan.scan_id,),
            ).fetchall()

        claimed = json.loads(scan["claimed_identity_json"])
        self.assertEqual(scan["completed_profile"], "normal")
        self.assertEqual(scan["stage"], "normal_speech_complete")
        self.assertEqual(int(transcript_count), 8)
        self.assertEqual(len(speech_evidence), 2)
        self.assertTrue(
            all(row["relation"] == "neutral" for row in speech_evidence)
        )
        self.assertTrue(
            all(float(row["strength"]) == 0.0 for row in speech_evidence)
        )
        self.assertTrue(
            all(
                row["correlation_group"] == "subtitle-dialogue:1"
                for row in speech_evidence
            )
        )
        self.assertTrue(claimed["normal_speech"]["escalated"])
        self.assertEqual(claimed["normal_speech"]["transcript_count"], 8)

    def test_targeted_speech_persists_candidate_evidence_in_dialogue_group(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class EpisodeTwoSpeech(FakeNormalSpeechEngine):
            def transcribe(self, _audio_path, request):
                self.calls += 1
                return SpeechTranscript(
                    text="bronze harbor lantern meadow quartz thunder",
                    language="en",
                )

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=EpisodeTwoSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertTrue(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 8)
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT candidate_key,evidence_category,relation,strength,
                          correlation_group,details_json,cache_key
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?
                   ORDER BY candidate_key""",
                (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
            ).fetchall()

        self.assertEqual(len(rows), 2)
        supported = next(
            row for row in rows
            if row["candidate_key"].endswith('"1002"]')
        )
        self.assertEqual(supported["evidence_category"], "speech")
        self.assertEqual(supported["relation"], "supports")
        self.assertGreater(float(supported["strength"]), 0.9)
        self.assertEqual(supported["correlation_group"], "subtitle-dialogue:1")
        self.assertTrue(supported["cache_key"])
        details = json.loads(supported["details_json"])
        self.assertEqual(details["transcript_count"], 8)
        self.assertEqual(details["correlated_with"], ["subtitle-synopsis"])
        self.assertEqual(len(details["windows"]), 8)

        review = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        speech_analysis = review["speech_analysis"]
        self.assertIsNotNone(speech_analysis)
        self.assertTrue(speech_analysis["escalated"])
        self.assertEqual(speech_analysis["text_transcript_count"], 8)
        self.assertEqual(len(speech_analysis["windows"]), 8)
        self.assertGreater(speech_analysis["strongest_similarity"], 0.9)
        self.assertIn("bronze harbor", speech_analysis["transcript_excerpt"])
        self.assertEqual(
            speech_analysis["correlation_group"],
            "subtitle-dialogue:1",
        )

    def test_spanish_only_audio_is_translated_before_english_synopsis_scoring(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class SpanishExtractor(FakeNormalSpeechExtractor):
            instances = []

            def __init__(self, media, streams, *, preferred_language=""):
                super().__init__(
                    media,
                    streams,
                    preferred_language=preferred_language,
                )
                self.stream = SpeechAudioStream(
                    index=0,
                    language="spa",
                    channels=2,
                    sample_rate_hz=48_000,
                    default=True,
                )

        class TranslatedEpisodeTwoSpeech(FakeNormalSpeechEngine):
            def __init__(self):
                super().__init__()
                self.requests = []

            def transcribe(self, _audio_path, request):
                self.calls += 1
                self.requests.append(request)
                return SpeechTranscript(
                    text="bronze harbor lantern meadow quartz thunder",
                    language="en",
                )

        speech_engine = TranslatedEpisodeTwoSpeech()
        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=speech_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=SpanishExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertTrue(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 8)
        self.assertEqual(len(speech_engine.requests), 8)
        self.assertTrue(
            all(request.language == "spa" for request in speech_engine.requests)
        )
        self.assertTrue(
            all(request.translate for request in speech_engine.requests)
        )
        self.assertEqual(
            SpanishExtractor.instances[-1].preferred_language,
            "eng",
        )

        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT candidate_key,relation,strength,details_json
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?
                   ORDER BY candidate_key""",
                (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
            ).fetchall()
        supported = next(
            row for row in rows
            if row["candidate_key"].endswith('"1002"]')
        )
        self.assertEqual(supported["relation"], "supports")
        self.assertGreater(float(supported["strength"]), 0.9)
        details = json.loads(supported["details_json"])
        self.assertEqual(details["synopsis_language"], "eng")
        self.assertEqual(details["transcript_languages"], ["eng"])
        review = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertTrue(review["snapshot_current"])

    def test_speech_corpus_excludes_transcript_in_wrong_synopsis_language(self):
        audio = SpeechAudioIdentity(
            sha256="b" * 64,
            size_bytes=1024,
            format_key="wav-pcm-s16le",
            sample_rate_hz=16_000,
            channels=1,
            source_signature="fixture-spanish",
        )
        run = NormalSpeechRun(
            observations=(
                NormalSpeechObservation(
                    window=SpeechWindow(0, 1000),
                    cache_key="spanish",
                    source_signature="fixture-spanish",
                    transcript=SpeechTranscript(
                        text="puerto bronce linterna pradera",
                        language="es",
                    ),
                    audio_identity=audio,
                    artifact_id=1,
                ),
            )
        )
        corpus, usable, excluded = NormalIdentityService._speech_text_corpus(
            run,
            "eng",
        )
        self.assertFalse(corpus.tokens)
        self.assertEqual(usable, [])
        self.assertEqual(len(excluded), 1)

    def test_padded_speech_text_remains_current_with_exact_cache_hash(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class PaddedSpeech(FakeNormalSpeechEngine):
            def transcribe(self, _audio_path, request):
                self.calls += 1
                return SpeechTranscript(
                    text="  bronze harbor lantern meadow quartz thunder  \n",
                    language="en",
                )

        NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=PaddedSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        detail = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertTrue(detail["snapshot_current"])
        self.assertIn(
            "bronze harbor lantern meadow quartz thunder",
            detail["speech_analysis"]["transcript_excerpt"],
        )

    def test_tokenless_nonblank_speech_stays_current_neutral_evidence(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class StopwordSpeech(FakeNormalSpeechEngine):
            def transcribe(self, _audio_path, request):
                self.calls += 1
                return SpeechTranscript(
                    text="this that with there",
                    language="en",
                )

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=StopwordSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertEqual(result.speech_text_transcript_count, 8)
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT relation,strength,cache_key,details_json
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?""",
                (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relation"], "neutral")
        self.assertEqual(float(rows[0]["strength"]), 0.0)
        self.assertTrue(rows[0]["cache_key"])
        details = json.loads(rows[0]["details_json"])
        self.assertEqual(len(details["artifact_ids"]), 8)
        self.assertEqual(len(details["windows"]), 8)
        self.assertIn("this that with there", details["transcript_excerpt"])

        detail = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertTrue(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_speech_corpus_does_not_create_cross_window_bigrams(self):
        audio = SpeechAudioIdentity(
            sha256="a" * 64,
            size_bytes=1024,
            format_key="wav-pcm-s16le",
            sample_rate_hz=16_000,
            channels=1,
            source_signature="fixture",
        )
        run = NormalSpeechRun(
            observations=(
                NormalSpeechObservation(
                    window=SpeechWindow(0, 1000),
                    cache_key="one",
                    source_signature="fixture-one",
                    transcript=SpeechTranscript(text="alpha cedar", language="en"),
                    audio_identity=audio,
                    artifact_id=1,
                ),
                NormalSpeechObservation(
                    window=SpeechWindow(2000, 3000),
                    cache_key="two",
                    source_signature="fixture-two",
                    transcript=SpeechTranscript(text="bravo delta", language="en"),
                    audio_identity=audio,
                    artifact_id=2,
                ),
            )
        )
        corpus, usable, excluded = NormalIdentityService._speech_text_corpus(
            run,
            "eng",
        )
        self.assertEqual(len(usable), 2)
        self.assertEqual(excluded, [])
        self.assertIn(("alpha", "cedar"), corpus.bigrams)
        self.assertIn(("bravo", "delta"), corpus.bigrams)
        self.assertNotIn(("cedar", "bravo"), corpus.bigrams)

    def test_reassigned_speech_candidate_makes_completed_normal_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class EpisodeTwoSpeech(FakeNormalSpeechEngine):
            def transcribe(self, _audio_path, request):
                self.calls += 1
                return SpeechTranscript(
                    text="bronze harbor lantern meadow quartz thunder",
                    language="en",
                )

        NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=EpisodeTwoSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        decisions = MediaIdentityDecisionService(self.database)
        self.assertTrue(
            decisions.scan_detail(self.fast_scan.scan_id)["snapshot_current"]
        )
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT id,candidate_key,relation
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?
                   ORDER BY id""",
                (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
            ).fetchall()
            supported = next(row for row in rows if row["relation"] == "supports")
            other = next(
                row for row in rows
                if row["candidate_key"] != supported["candidate_key"]
            )
            conn.execute(
                """UPDATE media_identity_evidence
                   SET candidate_key=?
                   WHERE id=?""",
                (other["candidate_key"], supported["id"]),
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_tampered_speech_strength_makes_completed_normal_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class EpisodeTwoSpeech(FakeNormalSpeechEngine):
            def transcribe(self, _audio_path, request):
                self.calls += 1
                return SpeechTranscript(
                    text="bronze harbor lantern meadow quartz thunder",
                    language="en",
                )

        NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=EpisodeTwoSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        decisions = MediaIdentityDecisionService(self.database)
        self.assertTrue(
            decisions.scan_detail(self.fast_scan.scan_id)["snapshot_current"]
        )
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_identity_evidence
                   SET strength=0.123456
                   WHERE scan_id=? AND analyzer_key=? AND relation='supports'""",
                (
                    self.fast_scan.scan_id,
                    NORMAL_SPEECH_EVIDENCE_KEY,
                ),
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_missing_speech_evidence_makes_completed_normal_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.speech_transcript_count, 8)

        decisions = MediaIdentityDecisionService(self.database)
        self.assertTrue(
            decisions.scan_detail(self.fast_scan.scan_id)["snapshot_current"]
        )
        with self.database.connect() as conn:
            conn.execute(
                """DELETE FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='speech-synopsis'""",
                (self.fast_scan.scan_id,),
            )

        stale = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_wrong_speech_correlation_group_makes_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.speech_transcript_count, 8)

        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_identity_evidence
                   SET correlation_group='speech-independent:1'
                   WHERE scan_id=? AND analyzer_key='speech-synopsis'""",
                (self.fast_scan.scan_id,),
            )

        stale = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_pre_i5_normal_metadata_without_speech_evidence_version_is_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            claimed["normal_speech"].pop("evidence_algorithm_version", None)
            conn.execute(
                """UPDATE media_identity_scans
                   SET claimed_identity_json=?
                   WHERE id=?""",
                (
                    json.dumps(claimed, sort_keys=True),
                    self.fast_scan.scan_id,
                ),
            )

        stale = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])

    def test_missing_speech_artifact_makes_completed_normal_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.speech_transcript_count, 8)

        decisions = MediaIdentityDecisionService(self.database)
        before = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertTrue(before["snapshot_current"])

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            artifact_id = claimed["normal_speech"]["artifact_ids"][0]
            conn.execute(
                "DELETE FROM media_identity_artifacts WHERE id=?",
                (artifact_id,),
            )

        after = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(after["snapshot_current"])
        self.assertFalse(after["actionable"])

    def test_error_speech_artifact_makes_completed_normal_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.speech_transcript_count, 8)

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            artifact_id = claimed["normal_speech"]["artifact_ids"][0]
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET status='error',error='fixture'
                   WHERE id=?""",
                (artifact_id,),
            )

        detail = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_malformed_speech_artifact_payload_makes_scan_stale(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.speech_transcript_count, 8)

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            claimed = json.loads(row["claimed_identity_json"])
            artifact_id = claimed["normal_speech"]["artifact_ids"][0]
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json='{}'
                   WHERE id=?""",
                (artifact_id,),
            )

        detail = MediaIdentityDecisionService(self.database).scan_detail(
            self.fast_scan.scan_id
        )
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_speech_only_rerun_preserves_prior_normal_ocr_evidence(self):
        class WeakPreview(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"unrelated"

        FakeNormalSpeechExtractor.instances.clear()
        first_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([WeakPreview()]),
            FakeOcr(),
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_preview_bytes_per_frame=9,
                max_preview_bytes_total=9,
            ),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        )
        first = first_service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.completed_profile.value, "normal")
        self.assertGreater(first.observation_count, 0)
        self.assertEqual(first.speech_transcript_count, 8)

        with self.database.connect() as conn:
            before_scan = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            before_evidence = [
                tuple(row)
                for row in conn.execute(
                    """SELECT candidate_key,relation,strength,source_kind,
                              source_ref,value_text,details_json,cache_key
                       FROM media_identity_evidence
                       WHERE scan_id=? AND analyzer_key=?
                       ORDER BY id""",
                    (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
                ).fetchall()
            ]

        before_claimed = json.loads(before_scan["claimed_identity_json"])
        self.assertTrue(before_evidence)
        self.assertIn("normal_ocr", before_claimed)

        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        second = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=FakeNormalSpeechEngine(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertEqual(second.completed_profile.value, "normal")
        self.assertEqual(second.observation_count, 0)
        self.assertEqual(second.speech_transcript_count, 8)
        self.assertEqual(second.speech_reused_artifact_count, 8)

        with self.database.connect() as conn:
            after_scan = conn.execute(
                """SELECT claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            after_evidence = [
                tuple(row)
                for row in conn.execute(
                    """SELECT candidate_key,relation,strength,source_kind,
                              source_ref,value_text,details_json,cache_key
                       FROM media_identity_evidence
                       WHERE scan_id=? AND analyzer_key=?
                       ORDER BY id""",
                    (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
                ).fetchall()
            ]

        after_claimed = json.loads(after_scan["claimed_identity_json"])
        self.assertEqual(after_evidence, before_evidence)
        self.assertEqual(
            after_claimed["normal_ocr"],
            before_claimed["normal_ocr"],
        )

    def test_second_normal_speech_run_reuses_persisted_fragments(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        FakeNormalSpeechExtractor.instances.clear()
        first_engine = FakeNormalSpeechEngine()
        first_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=first_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        )
        first = first_service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.speech_transcript_count, 8)
        self.assertEqual(first_engine.calls, 8)

        second_engine = FakeNormalSpeechEngine()
        second_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=second_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        )
        second = second_service.run_scan(self.fast_scan.scan_id)

        self.assertEqual(second.completed_profile.value, "normal")
        self.assertEqual(second.speech_transcript_count, 8)
        self.assertEqual(second.speech_reused_artifact_count, 8)
        self.assertEqual(second_engine.calls, 0)
        self.assertEqual(FakeNormalSpeechExtractor.instances[-1].extract_calls, 8)
        self.assertTrue(
            all(
                item.cleanup_calls == 1
                for item in FakeNormalSpeechExtractor.instances[-1].prepared
            )
        )

    def test_strong_existing_subtitle_signal_skips_speech_escalation(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        with self.database.connect() as conn:
            candidate = conn.execute(
                """SELECT candidate_key FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY rank,candidate_key LIMIT 1""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            conn.execute(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,source_ref,value_text,details_json,cache_key,profile
                   ) VALUES (
                     ?,?,'subtitle-synopsis','1','subtitle_text',
                     'subtitle-dialogue:1','supports',0.75,
                     'sidecar_subtitle','fixture.srt','fixture','{}','','fast'
                   )""",
                (self.fast_scan.scan_id, candidate["candidate_key"]),
            )

        self._reseal_scan_fixture()

        speech_engine = FakeNormalSpeechEngine()
        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=speech_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertFalse(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 0)
        self.assertEqual(speech_engine.calls, 0)

    def test_close_neutral_subtitle_runner_up_still_escalates_to_speech(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        with self.database.connect() as conn:
            candidates = conn.execute(
                """SELECT candidate_key FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY rank,candidate_key LIMIT 2""",
                (self.fast_scan.scan_id,),
            ).fetchall()
            self.assertEqual(len(candidates), 2)
            rows = [
                (
                    self.fast_scan.scan_id,
                    candidates[0]["candidate_key"],
                    "supports",
                    0.35,
                    json.dumps({"similarity": 0.35}),
                ),
                (
                    self.fast_scan.scan_id,
                    candidates[1]["candidate_key"],
                    "neutral",
                    0.0,
                    json.dumps({"similarity": 0.29}),
                ),
            ]
            conn.executemany(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,source_ref,value_text,details_json,cache_key,profile
                   ) VALUES (
                     ?,?,'subtitle-synopsis','1','subtitle_text',
                     'subtitle-dialogue:1',?,?, 'sidecar_subtitle',
                     'fixture.srt','fixture',?,'','fast'
                   )""",
                rows,
            )

        self._reseal_scan_fixture()

        FakeNormalSpeechExtractor.instances.clear()
        speech_engine = FakeNormalSpeechEngine()
        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=speech_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertTrue(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 8)
        self.assertEqual(speech_engine.calls, 8)

    def test_raw_subtitle_similarity_can_skip_speech_when_separation_is_clear(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        with self.database.connect() as conn:
            candidates = conn.execute(
                """SELECT candidate_key FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY rank,candidate_key LIMIT 2""",
                (self.fast_scan.scan_id,),
            ).fetchall()
            self.assertEqual(len(candidates), 2)
            rows = [
                (
                    self.fast_scan.scan_id,
                    candidates[0]["candidate_key"],
                    "supports",
                    0.55,
                    json.dumps({"similarity": 0.55}),
                ),
                (
                    self.fast_scan.scan_id,
                    candidates[1]["candidate_key"],
                    "neutral",
                    0.0,
                    json.dumps({"similarity": 0.20}),
                ),
            ]
            conn.executemany(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,source_ref,value_text,details_json,cache_key,profile
                   ) VALUES (
                     ?,?,'subtitle-synopsis','1','subtitle_text',
                     'subtitle-dialogue:1',?,?, 'sidecar_subtitle',
                     'fixture.srt','fixture',?,'','fast'
                   )""",
                rows,
            )

        self._reseal_scan_fixture()

        speech_engine = FakeNormalSpeechEngine()
        result = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=speech_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)

        self.assertFalse(result.speech_escalated)
        self.assertEqual(result.speech_transcript_count, 0)
        self.assertEqual(speech_engine.calls, 0)

    def test_partial_visual_rerun_cannot_replace_complete_normal_result(self):
        first = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource()]),
            FakeOcr(),
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.completed_profile.value, "normal")
        decisions = MediaIdentityDecisionService(self.database)
        decisions.resolve_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            before_scan = conn.execute(
                """SELECT claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            before_evidence = [
                tuple(row)
                for row in conn.execute(
                    """SELECT candidate_key,relation,strength,details_json,cache_key
                       FROM media_identity_evidence
                       WHERE scan_id=? AND analyzer_key=?
                       ORDER BY id""",
                    (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
                ).fetchall()
            ]

        class FiveFrameSource(FakePreviewSource):
            def preview_frames(self, _media):
                return tuple(
                    PreviewFrameRef(
                        source_key=self.source_key,
                        item_id="episode-1",
                        timestamp_ms=(index + 1) * 10_000,
                        asset_ref=f"partial:{index}",
                        source_signature="preview-partial-v1",
                        width=320,
                        height=180,
                    )
                    for index in range(5)
                )

        class OneThenFailOcr(FakeOcr):
            def recognize(self, image: bytes) -> OcrTextResult:
                self.calls += 1
                if self.calls > 1:
                    raise NormalIdentityError("fixture transient OCR failure")
                return OcrTextResult(
                    text=image.decode("utf-8"),
                    confidence=1.0,
                    details={"fixture": True},
                )

        with self.assertRaisesRegex(
            RuntimeError,
            "only completed part of its visual coverage",
        ):
            NormalIdentityService(
                self.database,
                ExternalSourceRegistry([FiveFrameSource()]),
                OneThenFailOcr(),
            ).run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            after_scan = conn.execute(
                """SELECT claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            after_evidence = [
                tuple(row)
                for row in conn.execute(
                    """SELECT candidate_key,relation,strength,details_json,cache_key
                       FROM media_identity_evidence
                       WHERE scan_id=? AND analyzer_key=?
                       ORDER BY id""",
                    (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
                ).fetchall()
            ]

        self.assertEqual(dict(after_scan), dict(before_scan))
        self.assertEqual(after_evidence, before_evidence)
        self.assertTrue(
            decisions.scan_detail(self.fast_scan.scan_id)["snapshot_current"]
        )

    def test_partial_speech_rerun_cannot_replace_complete_eight_window_result(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        class EpisodeTwoSpeech(FakeNormalSpeechEngine):
            def transcribe(self, _audio_path, request):
                self.calls += 1
                return SpeechTranscript(
                    text="bronze harbor lantern meadow quartz thunder",
                    language="en",
                )

        first = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=EpisodeTwoSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        ).run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.speech_transcript_count, 8)

        decisions = MediaIdentityDecisionService(self.database)
        decisions.resolve_scan(self.fast_scan.scan_id)
        with self.database.connect() as conn:
            before_scan = conn.execute(
                """SELECT claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            before_evidence = [
                tuple(row)
                for row in conn.execute(
                    """SELECT candidate_key,relation,strength,details_json,cache_key
                       FROM media_identity_evidence
                       WHERE scan_id=? AND analyzer_key=?
                       ORDER BY id""",
                    (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
                ).fetchall()
            ]

        class OneThenFailExtractor(FakeNormalSpeechExtractor):
            def extract(self, window):
                if self.extract_calls >= 1:
                    self.extract_calls += 1
                    raise RuntimeError("fixture transient speech extraction failure")
                return super().extract(window)

        with self.assertRaisesRegex(
            RuntimeError,
            "only completed part of the speech coverage",
        ):
            NormalIdentityService(
                self.database,
                ExternalSourceRegistry(()),
                UnavailableOcr(),
                speech_engine=EpisodeTwoSpeech(),
                speech_model=fake_normal_speech_model(),
                speech_extractor_factory=OneThenFailExtractor,
            ).run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            after_scan = conn.execute(
                """SELECT claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            after_evidence = [
                tuple(row)
                for row in conn.execute(
                    """SELECT candidate_key,relation,strength,details_json,cache_key
                       FROM media_identity_evidence
                       WHERE scan_id=? AND analyzer_key=?
                       ORDER BY id""",
                    (self.fast_scan.scan_id, NORMAL_SPEECH_EVIDENCE_KEY),
                ).fetchall()
            ]

        self.assertEqual(dict(after_scan), dict(before_scan))
        self.assertEqual(after_evidence, before_evidence)
        detail = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertTrue(detail["snapshot_current"])
        self.assertTrue(detail["actionable"])

    def test_weak_rerun_cannot_discard_existing_speech_backed_normal_state(self):
        class UnavailableOcr(FakeOcr):
            def available(self):
                return False

        first_engine = FakeNormalSpeechEngine()
        first_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry(()),
            UnavailableOcr(),
            speech_engine=first_engine,
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        )
        first = first_service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(first.speech_transcript_count, 8)

        class WeakPreview(FakePreviewSource):
            def read_preview(self, _frame):
                self.read_calls += 1
                return b"unrelated"

        class UnavailableSpeech(FakeNormalSpeechEngine):
            def __init__(self):
                super().__init__(available=False)

        weak = WeakPreview()
        second_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([weak]),
            FakeOcr(),
            limits=NormalResourceLimits(
                initial_preview_frames=1,
                expanded_preview_frames=1,
                max_preview_frames=1,
                max_preview_bytes_per_frame=9,
                max_preview_bytes_total=9,
            ),
            speech_engine=UnavailableSpeech(),
            speech_model=fake_normal_speech_model(),
            speech_extractor_factory=FakeNormalSpeechExtractor,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "existing completed Normal evidence was retained",
        ):
            second_service.run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT completed_profile,stage,claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            transcript_count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='speech_transcript'"""
            ).fetchone()["count"]

        claimed = json.loads(scan["claimed_identity_json"])
        self.assertEqual(scan["completed_profile"], "normal")
        self.assertEqual(scan["stage"], "normal_speech_complete")
        self.assertEqual(claimed["normal_speech"]["transcript_count"], 8)
        self.assertEqual(int(transcript_count), 8)

    def test_deleted_normal_ocr_artifact_stales_sealed_result(self):
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource()]),
            FakeOcr(),
        )
        result = service.run_scan(self.fast_scan.scan_id)
        self.assertEqual(result.observation_count, 1)

        decisions = MediaIdentityDecisionService(self.database)
        decisions.resolve_scan(self.fast_scan.scan_id)
        before = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertTrue(before["snapshot_current"])

        with self.database.connect() as conn:
            evidence = conn.execute(
                """SELECT details_json FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=?
                   ORDER BY id LIMIT 1""",
                (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
            ).fetchone()
            artifact_ids = json.loads(evidence["details_json"])["artifact_ids"]
            self.assertTrue(artifact_ids)
            conn.execute(
                "DELETE FROM media_identity_artifacts WHERE id=?",
                (int(artifact_ids[0]),),
            )

        after = decisions.scan_detail(self.fast_scan.scan_id)
        self.assertFalse(after["snapshot_current"])
        self.assertFalse(after["actionable"])
        self.assertEqual(
            decisions.rename_preview(self.fast_scan.scan_id)["status"],
            "stale",
        )

    def test_superseded_failed_normal_attempt_returns_persisted_winner(self):
        winner_source = FakePreviewSource()
        winner_service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([winner_source]),
            FakeOcr(),
        )
        winner_result = None

        class TriggerWinnerThenFail(FakeOcr):
            def recognize(inner_self, image):
                nonlocal winner_result
                inner_self.calls += 1
                if winner_result is None:
                    winner_result = winner_service.run_scan(
                        self.fast_scan.scan_id
                    )
                raise NormalIdentityError("fixture loser OCR failure")

        loser = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([FakePreviewSource()]),
            TriggerWinnerThenFail(),
        ).run_scan(self.fast_scan.scan_id)

        self.assertIsNotNone(winner_result)
        self.assertEqual(winner_result.completed_profile.value, "normal")
        self.assertEqual(loser.completed_profile.value, "normal")
        self.assertEqual(loser.observation_count, winner_result.observation_count)
        self.assertEqual(loser.text_observation_count, winner_result.text_observation_count)

        with self.database.connect() as conn:
            scan = conn.execute(
                """SELECT completed_profile,claimed_identity_json
                   FROM media_identity_scans WHERE id=?""",
                (self.fast_scan.scan_id,),
            ).fetchone()
            evidence_count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key=? AND profile='normal'""",
                (self.fast_scan.scan_id, NORMAL_OCR_EVIDENCE_KEY),
            ).fetchone()["count"]

        claimed = json.loads(scan["claimed_identity_json"])
        self.assertEqual(scan["completed_profile"], "normal")
        self.assertEqual(claimed["result_revision"], 2)
        self.assertGreater(int(evidence_count), 0)
        self.assertEqual(
            claimed["decision_snapshot"]["revision"],
            claimed["result_revision"],
        )

    def test_file_change_during_ocr_prevents_artifact_or_evidence_commit(self):
        def mutate_file():
            self.media_path.write_bytes(self.media_path.read_bytes() + b"changed")

        source = FakePreviewSource(mutate=mutate_file)
        service = NormalIdentityService(
            self.database,
            ExternalSourceRegistry([source]),
            FakeOcr(),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "inputs changed during Normal OCR",
        ):
            service.run_scan(self.fast_scan.scan_id)

        with self.database.connect() as conn:
            artifacts = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'"""
            ).fetchone()["count"]
            evidence = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_evidence
                   WHERE scan_id=? AND analyzer_key='preview-ocr-synopsis'""",
                (self.fast_scan.scan_id,),
            ).fetchone()["count"]
        self.assertEqual(int(artifacts), 0)
        self.assertEqual(int(evidence), 0)


if __name__ == "__main__":
    unittest.main()
