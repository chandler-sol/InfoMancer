from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.media_identity.candidates import generate_episode_candidates
from app.media_identity.decision_snapshot import (
    result_revision,
    seal_decision_snapshot,
)
from app.media_identity.deep_correlation_service import (
    DeepCorrelationAnalysisError,
    DeepCorrelationAnalysisService,
)
from app.media_identity.fast import (
    SCAN_INPUT_SIGNATURE_VERSION,
    FastIdentityService,
    combined_scan_input_signature,
    scan_input_signatures,
)
from app.media_identity.fingerprint import (
    AUDIO_ENVELOPE_DHASH64_V1,
    VIDEO_DHASH64_V1,
    ContentFingerprint,
    FingerprintSample,
)
from app.media_identity.fingerprint_audio import (
    plan_audio_fingerprint_timestamps,
)
from app.media_identity.fingerprint_audio_service import (
    DeepAudioFingerprintArtifactService,
    DeepAudioFingerprintCorrelationService,
)
from app.media_identity.fingerprint_correlation import (
    DeepFingerprintCorrelationService,
)
from app.media_identity.fingerprint_local import (
    plan_video_fingerprint_timestamps,
)
from app.media_identity.fingerprint_service import (
    DeepFingerprintArtifactService,
)
from app.media_identity.media_generation import (
    media_generation_identity,
)
from app.media_identity.models import (
    IdentityReference,
    IdentityResultState,
)
from app.media_identity.sequence_correlation import (
    SequenceHypothesis,
    SequenceOffsetAnalysis,
    SequenceOffsetPolicy,
)
from app.media_identity.sequence_correlation_service import (
    DeepSequenceCorrelationRun,
)
from app.media_identity.service import MediaIdentityDecisionService
from app.media_identity.versions import (
    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION,
)


class FakeVideoExtractor:
    calls = 0

    def __init__(self, media, runtime_ms) -> None:
        self.media = media
        self.runtime_ms = int(runtime_ms)
        self.timestamps = plan_video_fingerprint_timestamps(
            self.runtime_ms,
            sample_count=6,
        )
        self.generation = media_generation_identity(
            media.path
        )
        self.source_signature = hashlib.sha256(
            (
                f"j4-video:{media.file_id}:{media.sha256}:"
                f"{self.runtime_ms}:{self.generation}"
            ).encode()
        ).hexdigest()

    def available(self) -> bool:
        return True

    def extract(self) -> ContentFingerprint:
        type(self).calls += 1
        return ContentFingerprint(
            file_id=self.media.file_id,
            file_sha256=str(self.media.sha256),
            runtime_ms=self.runtime_ms,
            algorithm=VIDEO_DHASH64_V1,
            samples=tuple(
                FingerprintSample(
                    timestamp_ms=timestamp,
                    value=f"{index + 1:016x}",
                )
                for index, timestamp in enumerate(
                    self.timestamps
                )
            ),
            source_kind="local_ffmpeg",
            source_signature=self.source_signature,
            parameters={
                "extractor_version": 1,
                "media_generation": self.generation,
                "sample_count": len(self.timestamps),
                "filter": "fixture",
            },
            comparison_parameters={
                "sample_count": len(self.timestamps),
                "lattice": "interior-10-90",
                "filter": "fixture",
                "hash": "horizontal-dhash64",
            },
        )


class FakeAudioExtractor:
    calls = 0

    def __init__(
        self,
        media,
        runtime_ms,
        *,
        stream,
    ) -> None:
        self.media = media
        self.stream = stream
        self.runtime_ms = int(runtime_ms)
        self.timestamps = plan_audio_fingerprint_timestamps(
            self.runtime_ms,
            sample_count=6,
        )
        self.generation = media_generation_identity(
            media.path
        )
        self.source_signature = hashlib.sha256(
            (
                f"j4-audio:{media.file_id}:{media.sha256}:"
                f"{self.runtime_ms}:{dict(stream.cache_identity())}:"
                f"{self.generation}"
            ).encode()
        ).hexdigest()

    def available(self) -> bool:
        return True

    def extract(self) -> ContentFingerprint:
        type(self).calls += 1
        return ContentFingerprint(
            file_id=self.media.file_id,
            file_sha256=str(self.media.sha256),
            runtime_ms=self.runtime_ms,
            algorithm=AUDIO_ENVELOPE_DHASH64_V1,
            samples=tuple(
                FingerprintSample(
                    timestamp_ms=timestamp,
                    value=f"{index + 20:016x}",
                )
                for index, timestamp in enumerate(
                    self.timestamps
                )
            ),
            source_kind="local_ffmpeg_audio",
            source_signature=self.source_signature,
            parameters={
                "extractor_version": 1,
                "media_generation": self.generation,
                "sample_count": len(self.timestamps),
                "window_ms": 4_000,
                "sample_rate_hz": 8_000,
                "channels": 1,
                "stream": dict(
                    self.stream.cache_identity()
                ),
                "feature_bins": 33,
            },
            comparison_parameters={
                "sample_count": len(self.timestamps),
                "lattice": (
                    "interior-10-90-window-centers"
                ),
                "window_ms": 4_000,
                "sample_rate_hz": 8_000,
                "channels": 1,
                "feature_bins": 33,
                "features": (
                    "mean-abs+zero-crossing-dhash64"
                ),
            },
        )


class DeepCorrelationAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeVideoExtractor.calls = 0
        FakeAudioExtractor.calls = 0
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example"
        self.show_root.mkdir(parents=True)
        self.media = (
            self.show_root / "Example - S01E01.mkv"
        )
        self.media.write_bytes(b"j4-media" * 256)
        stat = self.media.stat()
        self.digest = hashlib.sha256(
            self.media.read_bytes()
        ).hexdigest()

        self.database = Database(self.root / "j4.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,
                     folder_path,tvdb_id
                   ) VALUES (
                     1,1,'tv','Example','Example',?,4242
                   )""",
                (str(self.show_root),),
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,
                     season,episode,name
                   ) VALUES (?,1,?,1,?,?)""",
                [
                    (1, 1001, 1, "Pilot"),
                    (2, 1002, 2, "Second"),
                ],
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,
                     size_bytes,modified_at,season,
                     episode_start,episode_end,parsed_title,
                     runtime_seconds,width,height,
                     video_codec,audio_codec,audio_channels,
                     bitrate,container,dynamic_range,
                     media_info_at,media_info_error,seen_scan
                   ) VALUES (
                     1,1,?,?,?,?,?,1,1,1,'Example',
                     600,1920,1080,'H264','AAC',2,
                     5000000,'MKV','SDR',
                     '2026-09-24T12:00:00','','fixture'
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
                """INSERT INTO media_streams(
                     file_id,stream_index,stream_type,codec,
                     language,title,channels,channel_layout,
                     sample_rate,default_flag,forced_flag,
                     hearing_impaired,visual_impaired,
                     commentary,disposition_json
                   ) VALUES (
                     1,1,'audio','aac','eng','Main',
                     2,'stereo',48000,1,0,0,0,0,'{}'
                   )"""
            )

        self.decision_service = MediaIdentityDecisionService(
            self.database
        )
        self.scan_id = self._insert_resolved_scan(stat)

        video_artifacts = DeepFingerprintArtifactService(
            self.database,
            extractor_factory=FakeVideoExtractor,
        )
        self.video_service = DeepFingerprintCorrelationService(
            self.database,
            artifact_service=video_artifacts,
        )

        def audio_factory(language: str):
            artifacts = DeepAudioFingerprintArtifactService(
                self.database,
                extractor_factory=FakeAudioExtractor,
                preferred_language=language,
            )
            return DeepAudioFingerprintCorrelationService(
                self.database,
                artifact_service=artifacts,
            )

        self.analysis_service = DeepCorrelationAnalysisService(
            self.database,
            video_service=self.video_service,
            audio_service_factory=audio_factory,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _insert_resolved_scan(self, stat) -> int:
        claimed_ref = IdentityReference(
            "episode",
            "tvdb",
            "1001",
            expected_episode_id=1,
            order_namespace="default",
            season=1,
            episode=1,
            display_name="Pilot",
        )
        other_ref = IdentityReference(
            "episode",
            "tvdb",
            "1002",
            expected_episode_id=2,
            order_namespace="default",
            season=1,
            episode=2,
            display_name="Second",
        )
        with self.database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO media_identity_scans(
                     file_id,identity_kind,requested_profile,
                     completed_profile,status,stage,
                     claimed_identity_json,file_size_bytes,
                     file_modified_at,file_sha256,
                     metadata_signature,completed_at
                   ) VALUES (
                     1,'episode','fast','fast','complete',
                     'fast_complete',?,?,?,?, 'fixture',
                     CURRENT_TIMESTAMP
                   )""",
                (
                    "{}",
                    stat.st_size,
                    stat.st_mtime,
                    self.digest,
                ),
            )
            scan_id = int(cursor.lastrowid)
            conn.executemany(
                """INSERT INTO media_identity_candidates(
                     scan_id,candidate_key,identity_kind,
                     provider,provider_item_id,
                     expected_episode_id,order_namespace,
                     season,episode,display_name,rank,
                     details_json
                   ) VALUES (
                     ?,?,'episode','tvdb',?,?,
                     'default',1,?,?,1,?
                   )""",
                [
                    (
                        scan_id,
                        claimed_ref.content_key,
                        "1001",
                        1,
                        1,
                        "Pilot",
                        json.dumps({
                            "origins": [
                                "claimed_coordinate",
                                "same_season",
                            ],
                            "mappings": [{
                                "order_namespace": "default",
                                "order_name": "Default",
                                "season": 1,
                                "episode": 1,
                            }],
                        }),
                    ),
                    (
                        scan_id,
                        other_ref.content_key,
                        "1002",
                        2,
                        2,
                        "Second",
                        json.dumps({
                            "origins": ["same_season"],
                            "mappings": [{
                                "order_namespace": "default",
                                "order_name": "Default",
                                "season": 1,
                                "episode": 2,
                            }],
                        }),
                    ),
                ],
            )
            conn.executemany(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,
                     analyzer_version,evidence_category,
                     correlation_group,relation,strength,
                     source_kind,profile
                   ) VALUES (
                     ?,?,'fixture','1',?,?,
                     'supports',?,'fixture','fast'
                   )""",
                [
                    (
                        scan_id,
                        claimed_ref.content_key,
                        "claimed_identity",
                        "claim",
                        0.35,
                    ),
                    (
                        scan_id,
                        claimed_ref.content_key,
                        "container_metadata",
                        "runtime",
                        0.18,
                    ),
                    (
                        scan_id,
                        other_ref.content_key,
                        "subtitle_text",
                        "dialogue",
                        0.88,
                    ),
                    (
                        scan_id,
                        other_ref.content_key,
                        "container_metadata",
                        "runtime",
                        0.30,
                    ),
                ],
            )
            file_row = FastIdentityService._file_row(
                conn,
                1,
            )
            streams = FastIdentityService._stream_rows(
                conn,
                1,
            )
            candidate_set = generate_episode_candidates(
                conn,
                title_id=1,
                season=1,
                episode_start=1,
                episode_end=1,
                include_specials=False,
                language="eng",
            )
            signatures = scan_input_signatures(
                file_row,
                streams,
                candidate_set,
                [],
                language="eng",
                expanded_specials=False,
            )
            claimed = {
                "identity_kind": "episode",
                "season": 1,
                "episode_start": 1,
                "episode_end": 1,
                "filename": self.media.name,
                "scan_language": "eng",
                "expanded_specials": False,
                "input_signature_version": (
                    SCAN_INPUT_SIGNATURE_VERSION
                ),
                "input_signatures": signatures,
                "decision_algorithm_version": (
                    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION
                ),
            }
            conn.execute(
                """UPDATE media_identity_scans
                   SET claimed_identity_json=?,
                       metadata_signature=?
                   WHERE id=?""",
                (
                    json.dumps(claimed, sort_keys=True),
                    combined_scan_input_signature(
                        signatures
                    ),
                    scan_id,
                ),
            )
            seal_decision_snapshot(
                conn,
                scan_id,
                revision=1,
            )

        result = self.decision_service.resolve_scan(
            scan_id
        )
        self.assertEqual(
            result.best_candidate_key,
            other_ref.content_key,
        )
        return scan_id

    def _scan_state(self):
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT requested_profile,completed_profile,
                          status,stage,claimed_identity_json,
                          result_state,best_candidate_key
                   FROM media_identity_scans WHERE id=?""",
                (self.scan_id,),
            ).fetchone()
        return dict(row)

    def test_complete_j4_artifact_is_cached_and_does_not_mutate_scan(self) -> None:
        before = self._scan_state()

        first = self.analysis_service.run(
            self.scan_id
        )
        second = self.analysis_service.run(
            self.scan_id
        )

        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(
            second.artifact_id,
            first.artifact_id,
        )
        self.assertEqual(
            self._scan_state(),
            before,
        )
        self.assertEqual(FakeVideoExtractor.calls, 1)
        self.assertEqual(FakeAudioExtractor.calls, 1)
        self.assertEqual(
            first.patterns.patterns.duplicate_observations,
            (),
        )
        self.assertEqual(
            first.patterns.patterns.swap_observations,
            (),
        )

        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT artifact_type,analyzer_key,
                          status,profile,payload_json
                   FROM media_identity_artifacts
                   WHERE id=?""",
                (first.artifact_id,),
            ).fetchone()
        self.assertEqual(
            row["artifact_type"],
            "deep_correlation_analysis",
        )
        self.assertEqual(
            row["analyzer_key"],
            "deep-correlation-analysis",
        )
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["profile"], "deep")
        payload = json.loads(row["payload_json"])
        self.assertTrue(payload["analysis_complete"])
        coverage = payload["output"]["coverage"]
        self.assertEqual(
            coverage["complete_modalities"],
            ["video", "audio"],
        )
        self.assertTrue(coverage["fully_multimodal"])
        self.assertEqual(
            coverage["planned_pair_count"],
            first.interpretation.planned_pair_count,
        )
        self.assertEqual(
            coverage["interpreted_pair_count"],
            first.interpretation.pair_count,
        )
        self.assertEqual(
            coverage["planned_file_count"],
            first.sequence.planned_file_count,
        )
        self.assertEqual(
            coverage["valid_hypothesis_count"],
            first.sequence.hypothesis_count,
        )
        self.assertTrue(
            coverage["sequence_peer_coverage_complete"]
        )
        seal = payload.pop("artifact_output_sha256")
        self.assertEqual(
            seal,
            hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        )

    def test_scan_detail_exposes_only_current_sealed_j4_artifact(self) -> None:
        run = self.analysis_service.run(
            self.scan_id
        )

        detail = self.decision_service.scan_detail(
            self.scan_id
        )
        deep = detail["deep_correlation_analysis"]

        self.assertIsNotNone(deep)
        self.assertEqual(
            deep["artifact_id"],
            run.artifact_id,
        )
        self.assertTrue(deep["target_current"])
        self.assertTrue(deep["analysis_complete"])
        self.assertTrue(
            deep["coverage"]["fully_multimodal"]
        )
        self.assertEqual(
            deep["coverage"]["complete_modalities"],
            ("video", "audio"),
        )
        self.assertEqual(
            deep["coverage"]["planned_file_count"],
            run.sequence.planned_file_count,
        )
        self.assertEqual(
            deep["coverage"]["valid_hypothesis_count"],
            run.sequence.hypothesis_count,
        )

    def test_scan_detail_ignores_tampered_j4_artifact(self) -> None:
        run = self.analysis_service.run(
            self.scan_id
        )
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT payload_json
                   FROM media_identity_artifacts
                   WHERE id=?""",
                (run.artifact_id,),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["output"]["coverage"]["fully_multimodal"] = False
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json=?
                   WHERE id=?""",
                (
                    json.dumps(payload, sort_keys=True),
                    run.artifact_id,
                ),
            )

        detail = self.decision_service.scan_detail(
            self.scan_id
        )

        self.assertIsNone(
            detail["deep_correlation_analysis"]
        )

    def test_tampered_j4_artifact_is_repaired_from_current_inputs(self) -> None:
        first = self.analysis_service.run(
            self.scan_id
        )
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT payload_json
                   FROM media_identity_artifacts WHERE id=?""",
                (first.artifact_id,),
            ).fetchone()
            payload = json.loads(row["payload_json"])
            payload["analysis_complete"] = False
            conn.execute(
                """UPDATE media_identity_artifacts
                   SET payload_json=? WHERE id=?""",
                (
                    json.dumps(payload, sort_keys=True),
                    first.artifact_id,
                ),
            )

        second = self.analysis_service.run(
            self.scan_id
        )

        self.assertFalse(second.reused)
        self.assertEqual(
            second.artifact_id,
            first.artifact_id,
        )
        with self.database.connect() as conn:
            repaired = json.loads(conn.execute(
                """SELECT payload_json
                   FROM media_identity_artifacts WHERE id=?""",
                (first.artifact_id,),
            ).fetchone()["payload_json"])
        self.assertTrue(repaired["analysis_complete"])

    def test_final_publication_rejects_superseded_peer_hypothesis(self) -> None:
        policy = SequenceOffsetPolicy()
        target = SequenceHypothesis(
            file_id=1,
            scan_id=self.scan_id,
            result_revision=1,
            claimed_season=1,
            claimed_episode=1,
            claimed_episode_end=1,
            candidate_key="candidate:target",
            hypothesis_season=1,
            hypothesis_episode=2,
            result_state=IdentityResultState.LIKELY_MISMATCH,
            support_strength=0.75,
            conflict_strength=0.10,
            margin=0.20,
            independent_categories=2,
            content_support=True,
        )
        peer = SequenceHypothesis(
            file_id=2,
            scan_id=200,
            result_revision=1,
            claimed_season=1,
            claimed_episode=2,
            claimed_episode_end=2,
            candidate_key="candidate:peer",
            hypothesis_season=1,
            hypothesis_episode=1,
            result_state=IdentityResultState.LIKELY_MISMATCH,
            support_strength=0.75,
            conflict_strength=0.10,
            margin=0.20,
            independent_categories=2,
            content_support=True,
        )
        newer_peer = replace(
            peer,
            scan_id=201,
            candidate_key="candidate:newer-peer",
        )
        analysis = SequenceOffsetAnalysis(
            policy=policy,
            hypothesis_count=2,
            usable_count=2,
            excluded_file_ids=(),
            observations=(),
            conflicted_seasons=(),
        )
        policy_payload = policy.identity_payload()
        policy_signature = hashlib.sha256(
            json.dumps(
                policy_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        sequence = DeepSequenceCorrelationRun(
            scan_id=self.scan_id,
            target_file_id=1,
            result_revision=1,
            correlation_plan_signature="a" * 64,
            sequence_policy_signature=policy_signature,
            sequence_policy_identity=policy_payload,
            planned_file_count=2,
            hypothesis_count=2,
            missing_scan_file_ids=(),
            invalid_scan_file_ids=(),
            hypotheses=(target, peer),
            analysis=analysis,
        )
        plan = SimpleNamespace(
            files=(
                SimpleNamespace(file_id=1),
                SimpleNamespace(file_id=2),
            )
        )

        with patch.object(
            self.analysis_service.sequence_service,
            "_validated_hypothesis",
            return_value=target,
        ), patch.object(
            self.analysis_service.sequence_service,
            "_best_current_hypothesis",
            return_value=(newer_peer, True),
        ):
            valid = self.analysis_service._validate_peer_hypotheses(
                None,
                plan=plan,
                sequence=sequence,
                target_file_id=1,
            )

        self.assertFalse(valid)

    def test_final_publication_rejects_invalid_j3_matrix(self) -> None:
        self.analysis_service.run(self.scan_id)

        with patch.object(
            self.video_service,
            "validate_manifest_artifact",
            return_value=False,
        ):
            with self.assertRaisesRegex(
                DeepCorrelationAnalysisError,
                "video fingerprint matrix failed final validation",
            ):
                self.analysis_service.run(
                    self.scan_id
                )

    def test_target_change_before_persist_blocks_j4_publication(self) -> None:
        original = self.analysis_service._persist

        def mutate_then_persist(**kwargs):
            with self.database.connect() as conn:
                row = conn.execute(
                    """SELECT claimed_identity_json
                       FROM media_identity_scans WHERE id=?""",
                    (self.scan_id,),
                ).fetchone()
                claimed = json.loads(
                    row["claimed_identity_json"]
                )
                claimed["result_revision"] = (
                    int(
                        claimed.get(
                            "result_revision"
                        ) or 0
                    )
                    + 1
                )
                conn.execute(
                    """UPDATE media_identity_scans
                       SET claimed_identity_json=?
                       WHERE id=?""",
                    (
                        json.dumps(
                            claimed,
                            sort_keys=True,
                        ),
                        self.scan_id,
                    ),
                )
            return original(**kwargs)

        with patch.object(
            self.analysis_service,
            "_persist",
            side_effect=mutate_then_persist,
        ):
            with self.assertRaisesRegex(
                DeepCorrelationAnalysisError,
                "target scan is stale|publication changed",
            ):
                self.analysis_service.run(
                    self.scan_id
                )

        with self.database.connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*)
                   FROM media_identity_artifacts
                   WHERE file_id=1
                     AND artifact_type='deep_correlation_analysis'"""
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
