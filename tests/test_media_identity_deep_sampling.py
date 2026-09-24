from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.deep_sampling import (
    MAX_DEEP_VISUAL_FRAMES,
    DeepSamplingError,
    DeepSamplingPolicy,
    build_deep_visual_plan,
)
from app.media_identity.deep_sampling_service import (
    DeepSamplingScanError,
    DeepSamplingService,
)
from app.media_identity.external import (
    ExternalCapability,
    ExternalMediaRef,
    ExternalSourceStatus,
    PreviewFrameRef,
)
from app.media_identity.fast import FastIdentityService
from app.media_identity.local_frames import LOCAL_FRAME_SOURCE_KEY
from app.media_identity.normal import (
    NormalIdentityError,
    NormalResourceLimits,
    OcrTextResult,
    select_staged_preview_frames,
)


def _frames(
    *,
    count: int = 40,
    signature: str = "source-v1",
) -> tuple[PreviewFrameRef, ...]:
    return tuple(
        PreviewFrameRef(
            source_key=LOCAL_FRAME_SOURCE_KEY,
            item_id="file:1",
            timestamp_ms=(index + 1) * 10_000,
            asset_ref=f"fixture:{index + 1}",
            source_signature=signature,
            width=1280,
            height=720,
        )
        for index in range(count)
    )


class DeepSamplingPlanTests(unittest.TestCase):
    def test_default_plan_preserves_normal_samples_then_adds_coverage(self) -> None:
        frames = _frames()
        normal = select_staged_preview_frames(
            frames,
            limits=NormalResourceLimits(),
        )
        deep = build_deep_visual_plan(frames)

        self.assertEqual(len(normal), 12)
        self.assertEqual(len(deep.samples), 24)
        normal_identities = [
            (
                item.frame.timestamp_ms,
                item.frame.asset_ref,
                item.frame.source_signature,
            )
            for item in normal
        ]
        inherited = [
            (
                item.frame.timestamp_ms,
                item.frame.asset_ref,
                item.frame.source_signature,
            )
            for item in deep.samples
            if item.inherited_normal
        ]
        self.assertEqual(inherited, normal_identities)
        self.assertEqual(
            len({item.work_key for item in deep.samples}),
            len(deep.samples),
        )
        self.assertEqual(len(deep.plan_signature), 64)

    def test_plan_is_deterministic_and_source_bound(self) -> None:
        policy = DeepSamplingPolicy(visual_frame_count=18)
        first = build_deep_visual_plan(
            tuple(reversed(_frames())),
            policy=policy,
        )
        second = build_deep_visual_plan(
            _frames(),
            policy=policy,
        )
        changed = build_deep_visual_plan(
            _frames(signature="source-v2"),
            policy=policy,
        )

        self.assertEqual(first.plan_signature, second.plan_signature)
        self.assertEqual(
            [item.work_key for item in first.samples],
            [item.work_key for item in second.samples],
        )
        self.assertNotEqual(first.plan_signature, changed.plan_signature)

    def test_policy_fails_closed_on_unbounded_work(self) -> None:
        bad = (
            {"visual_frame_count": MAX_DEEP_VISUAL_FRAMES + 1},
            {"visual_frame_count": True},
            {"max_preview_bytes_per_frame": 0},
            {"max_preview_bytes_total": 1},
            {"max_source_bytes_total": 1},
            {"max_ocr_text_chars": 0},
        )
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DeepSamplingError):
                    DeepSamplingPolicy(**kwargs)


class FakeDeepOcr:
    key = "fake-deep-ocr"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0
        self.successful_calls = 0
        self.interrupt_after: int | None = None

    def available(self) -> bool:
        return True

    def cache_identity(self):
        return {"fixture": "deep-ocr-v1", "deterministic": True}

    def recognize(self, image: bytes) -> OcrTextResult:
        self.calls += 1
        if (
            self.interrupt_after is not None
            and self.successful_calls >= self.interrupt_after
        ):
            raise KeyboardInterrupt()
        self.successful_calls += 1
        return OcrTextResult(
            text=image.decode("utf-8"),
            confidence=0.9,
            details={"fixture": True},
        )


class FakeDeepFrameSource:
    source_key = LOCAL_FRAME_SOURCE_KEY
    version = "fixture"

    def __init__(
        self,
        context,
        runtime_seconds,
        *,
        signature: str,
        mutate=None,
    ) -> None:
        self.context = context
        self.runtime_seconds = runtime_seconds
        self.signature = signature
        self.mutate = mutate
        self.read_calls = 0
        self.closed = False

    def status(self):
        return ExternalSourceStatus(
            source_key=self.source_key,
            available=True,
            capabilities=frozenset({ExternalCapability.PREVIEW_FRAMES}),
            detail="fixture",
        )

    def resolve_media(self, _context):
        return ExternalMediaRef(
            source_key=self.source_key,
            item_id=f"file:{int(self.context.media.file_id)}",
            path=str(self.context.media.path),
            source_signature=self.signature,
        )

    def preview_frames(self, _media):
        return _frames(signature=self.signature)

    def read_preview(self, frame):
        self.read_calls += 1
        if self.mutate is not None:
            self.mutate(self.read_calls)
        return f"deep-frame:{frame.timestamp_ms}".encode("utf-8")

    def close(self):
        self.closed = True


class FakeDeepFrameFactory:
    def __init__(self, signature: str = "source-v1", mutate=None) -> None:
        self.signature = signature
        self.mutate = mutate
        self.instances: list[FakeDeepFrameSource] = []

    def __call__(self, context, runtime_seconds):
        instance = FakeDeepFrameSource(
            context,
            runtime_seconds,
            signature=self.signature,
            mutate=self.mutate,
        )
        self.instances.append(instance)
        return instance


class DeepSamplingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example Show"
        self.show_root.mkdir(parents=True)
        self.media = self.show_root / "Example Show - S01E01.mkv"
        self.media.write_bytes(b"fixture-media" * 64)
        stat = self.media.stat()

        self.database = Database(self.root / "deep-sampling.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,folder_path,tvdb_id
                   ) VALUES (
                     1,1,'tv','Example Show','Example Show',?,4242
                   )""",
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
                    str(self.media),
                    self.media.name,
                    "mkv",
                    stat.st_size,
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
            conn.executemany(
                """INSERT INTO provider_episode_identities(
                     provider,provider_series_id,provider_episode_id,language,
                     name,overview,aired,metadata_json
                   ) VALUES ('tvdb','4242',?,'eng',?,?,?,'{}')""",
                [
                    (
                        "1001",
                        "Episode One",
                        "amber falcon orchard glacier velvet compass",
                        "2026-01-01",
                    ),
                    (
                        "1002",
                        "Episode Two",
                        "bronze harbor lantern meadow quartz thunder",
                        "2026-01-08",
                    ),
                ],
            )
            conn.executemany(
                """INSERT INTO provider_episode_mappings(
                     provider,provider_series_id,provider_episode_id,language,
                     order_namespace,order_name,season,episode,absolute_number,
                     coordinate_key,details_json
                   ) VALUES (
                     'tvdb','4242',?,'eng','default','Default',1,?,?,?,'{}'
                   )""",
                [
                    ("1001", 1, 1, "[1,1,1]"),
                    ("1002", 2, 2, "[1,2,2]"),
                ],
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,?,?,?,?,?)""",
                [
                    (1, 1, 1001, 1, 1, "Episode One"),
                    (2, 1, 1002, 1, 2, "Episode Two"),
                ],
            )

        self.fast = FastIdentityService(self.database)
        self.scan = self.fast.scan_file(1)
        self.policy = DeepSamplingPolicy(visual_frame_count=6)
        self.engine = FakeDeepOcr()
        self.factory = FakeDeepFrameFactory()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _service(self) -> DeepSamplingService:
        return DeepSamplingService(
            self.database,
            self.engine,
            policy=self.policy,
            frame_source_factory=self.factory,
        )

    def _scan_snapshot(self):
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT requested_profile,completed_profile,stage,
                          claimed_identity_json,result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.scan.scan_id,),
            ).fetchone()
        return tuple(row)

    def test_complete_run_checkpoints_artifacts_without_mutating_scan(self) -> None:
        before = self._scan_snapshot()
        result = self._service().run(self.scan.scan_id)
        after = self._scan_snapshot()

        self.assertTrue(result.coverage_complete)
        self.assertEqual(result.planned_frame_count, 6)
        self.assertEqual(result.completed_frame_count, 6)
        self.assertEqual(result.reused_artifact_count, 0)
        self.assertIsNotNone(result.manifest_artifact_id)
        self.assertEqual(before, after)
        self.assertEqual(self.engine.successful_calls, 6)
        self.assertEqual(self.factory.instances[-1].read_calls, 6)

        with self.database.connect() as conn:
            visual_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'
                     AND profile='deep'"""
            ).fetchone()[0]
            manifest_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='deep_sampling_manifest'"""
            ).fetchone()[0]
        self.assertEqual(visual_count, 6)
        self.assertEqual(manifest_count, 1)

        second = self._service().run(self.scan.scan_id)
        self.assertTrue(second.coverage_complete)
        self.assertEqual(second.reused_artifact_count, 6)
        self.assertEqual(self.factory.instances[-1].read_calls, 0)
        self.assertEqual(self.engine.successful_calls, 6)
        self.assertEqual(
            second.manifest_artifact_id,
            result.manifest_artifact_id,
        )

    def test_interrupted_run_resumes_exact_completed_work(self) -> None:
        before = self._scan_snapshot()
        self.engine.interrupt_after = 3
        with self.assertRaises(KeyboardInterrupt):
            self._service().run(self.scan.scan_id)

        self.assertEqual(before, self._scan_snapshot())
        with self.database.connect() as conn:
            visual_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'
                     AND profile='deep'"""
            ).fetchone()[0]
            manifest_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='deep_sampling_manifest'"""
            ).fetchone()[0]
        self.assertEqual(visual_count, 3)
        self.assertEqual(manifest_count, 0)

        self.engine.interrupt_after = None
        resumed = self._service().run(self.scan.scan_id)
        self.assertTrue(resumed.coverage_complete)
        self.assertEqual(resumed.reused_artifact_count, 3)
        self.assertEqual(self.factory.instances[-1].read_calls, 3)
        self.assertEqual(self.engine.successful_calls, 6)
        self.assertEqual(before, self._scan_snapshot())

    def test_source_signature_change_creates_a_new_exact_plan(self) -> None:
        first = self._service().run(self.scan.scan_id)
        self.assertTrue(first.coverage_complete)

        self.factory.signature = "source-v2"
        second = self._service().run(self.scan.scan_id)

        self.assertTrue(second.coverage_complete)
        self.assertNotEqual(first.plan_signature, second.plan_signature)
        self.assertNotEqual(
            first.manifest_artifact_id,
            second.manifest_artifact_id,
        )
        self.assertEqual(second.reused_artifact_count, 0)
        self.assertEqual(self.factory.instances[-1].read_calls, 6)

    def test_manifest_artifact_tamper_fails_closed(self) -> None:
        result = self._service().run(self.scan.scan_id)
        self.assertTrue(result.coverage_complete)
        assert result.manifest_artifact_id is not None

        with self.database.connect() as conn:
            manifest = conn.execute(
                """SELECT payload_json FROM media_identity_artifacts
                   WHERE id=?""",
                (result.manifest_artifact_id,),
            ).fetchone()
            payload = json.loads(manifest["payload_json"])
            child_id = int(payload["observations"][0]["artifact_id"])
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET text_value=text_value || '-tampered'
                   WHERE id=?""",
                (child_id,),
            )

        with self.assertRaisesRegex(
            DeepSamplingScanError,
            "artifact integrity",
        ):
            self._service().run(self.scan.scan_id)

    def test_publication_change_mid_run_keeps_partial_work_non_authoritative(self) -> None:
        before = self._scan_snapshot()
        mutated = {"done": False}

        def mutate(read_calls: int) -> None:
            if read_calls != 2 or mutated["done"]:
                return
            mutated["done"] = True
            with self.database.connect() as conn:
                row = conn.execute(
                    """SELECT claimed_identity_json
                       FROM media_identity_scans WHERE id=?""",
                    (self.scan.scan_id,),
                ).fetchone()
                claimed = json.loads(row["claimed_identity_json"])
                claimed["fixture_concurrent_change"] = True
                conn.execute(
                    """UPDATE media_identity_scans
                       SET claimed_identity_json=?
                       WHERE id=?""",
                    (
                        json.dumps(claimed, sort_keys=True),
                        self.scan.scan_id,
                    ),
                )

        self.factory.mutate = mutate
        with self.assertRaisesRegex(
            DeepSamplingScanError,
            "changed during Deep sampling|publication changed",
        ):
            self._service().run(self.scan.scan_id)

        with self.database.connect() as conn:
            manifest_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='deep_sampling_manifest'"""
            ).fetchone()[0]
            visual_count = conn.execute(
                """SELECT COUNT(*) FROM media_identity_artifacts
                   WHERE file_id=1 AND artifact_type='visual_text'
                     AND profile='deep'"""
            ).fetchone()[0]
        self.assertEqual(manifest_count, 0)
        self.assertGreaterEqual(visual_count, 1)
        self.assertNotEqual(before, self._scan_snapshot())


if __name__ == "__main__":
    unittest.main()
