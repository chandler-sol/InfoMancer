from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.media_identity.external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalSourceRegistry,
    ExternalSourceStatus,
    PreviewFrameRef,
)
from app.media_identity.fast import FastIdentityService
from app.media_identity.normal import OcrTextResult
from app.media_identity.local_frames import LOCAL_FRAME_SOURCE_KEY
from app.media_identity.normal_service import NormalIdentityService


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

    def test_second_normal_run_reuses_derived_ocr_without_rereading_preview(self):
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
        self.assertEqual(source.read_calls, 1)
        self.assertEqual(engine.calls, 1)
        with self.database.connect() as conn:
            artifact_count = conn.execute(
                """SELECT COUNT(*) AS count
                   FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'"""
            ).fetchone()["count"]
        self.assertEqual(int(artifact_count), 1)

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
                return b"bronze harbor lantern meadow quartz"

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
