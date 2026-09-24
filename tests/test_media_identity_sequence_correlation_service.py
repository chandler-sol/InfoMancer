from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db import Database
from app.media_identity.correlation_interpretation import (
    CorrelationInterpretationPolicy,
)
from app.media_identity.correlation_interpretation_service import (
    DeepCorrelationInterpretationRun,
)
from app.media_identity.decision_snapshot import result_revision
from app.media_identity.deep import plan_deep_correlation
from app.media_identity.fast import FastIdentityService
from app.media_identity.models import IdentityResultState
from app.media_identity.scoring import (
    CandidateResolution,
    IdentityResolution,
)
from app.media_identity.sequence_correlation import SequenceHypothesis
from app.media_identity.sequence_correlation_service import (
    DeepSequenceCorrelationError,
    DeepSequenceCorrelationService,
)
from app.media_identity.service import MediaIdentityDecisionService


def _interpretation_policy_identity():
    payload = CorrelationInterpretationPolicy().identity_payload()
    signature = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return payload, signature


def _hypothesis(
    file_id: int,
    claimed_episode: int,
    hypothesis_episode: int,
    *,
    state: IdentityResultState = IdentityResultState.LIKELY_MISMATCH,
) -> SequenceHypothesis:
    return SequenceHypothesis(
        file_id=file_id,
        scan_id=1000 + file_id,
        result_revision=4,
        claimed_season=1,
        claimed_episode=claimed_episode,
        claimed_episode_end=claimed_episode,
        candidate_key=f"candidate:{file_id}",
        hypothesis_season=1,
        hypothesis_episode=hypothesis_episode,
        result_state=state,
        support_strength=0.75,
        conflict_strength=0.10,
        margin=0.20,
        independent_categories=2,
        content_support=True,
    )


class DeepSequenceCorrelationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example"
        self.show_root.mkdir(parents=True)

        self.database = Database(self.root / "sequence.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,folder_path
                   ) VALUES (1,1,'tv','Example','Example',?)""",
                (str(self.show_root),),
            )
            for file_id in range(1, 5):
                path = self.show_root / f"Example - S01E{file_id:02d}.mkv"
                path.write_bytes(
                    (f"sequence-{file_id}".encode()) * 128
                )
                stat = path.stat()
                conn.execute(
                    """INSERT INTO files(
                         id,title_id,path,filename,extension,size_bytes,modified_at,
                         season,episode_start,episode_end,parsed_title,runtime_seconds,
                         width,height,video_codec,audio_codec,audio_channels,bitrate,
                         container,dynamic_range,media_info_at,media_info_error,
                         seen_scan
                       ) VALUES (
                         ?,1,?,?,?,?,?,1,?,?, 'Example',600,1920,1080,
                         'H264','AAC',2,5000000,'MKV','SDR',
                         '2026-09-24T12:00:00','','fixture'
                       )""",
                    (
                        file_id,
                        str(path),
                        path.name,
                        "mkv",
                        stat.st_size,
                        stat.st_mtime,
                        file_id,
                        file_id,
                    ),
                )
                conn.execute(
                    """INSERT INTO expected_episodes(
                         id,title_id,tvdb_episode_id,season,episode,name
                       ) VALUES (?,?,?,?,?,?)""",
                    (
                        file_id,
                        1,
                        3000 + file_id,
                        1,
                        file_id,
                        f"Episode {file_id}",
                    ),
                )

        self.fast = FastIdentityService(self.database)
        self.scan = self.fast.scan_file(1)
        with self.database.connect() as conn:
            self.scan_row = dict(conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (self.scan.scan_id,),
            ).fetchone())
            self.plan = plan_deep_correlation(
                conn,
                file_id=1,
            )
        self.revision = result_revision(self.scan_row)
        policy_identity, policy_signature = (
            _interpretation_policy_identity()
        )
        self.interpretation = DeepCorrelationInterpretationRun(
            scan_id=self.scan.scan_id,
            result_revision=self.revision,
            correlation_plan_signature=self.plan.plan_signature,
            interpretation_version=1,
            policy_signature=policy_signature,
            policy_identity=policy_identity,
            complete_modalities=(),
            planned_pair_count=len(self.plan.comparison_pairs),
            pairs=(),
        )
        self.service = DeepSequenceCorrelationService(
            self.database,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_coordinate_prefers_expected_episode_identity(self) -> None:
        candidate = {
            "expected_episode_id": 2,
            "order_namespace": "alternate",
            "season": 9,
            "episode": 9,
            "details": {
                "mappings": [
                    {
                        "order_namespace": "default",
                        "season": 1,
                        "episode": 3,
                    }
                ]
            },
        }
        with self.database.connect() as conn:
            coordinate = self.service._candidate_default_coordinate(
                conn,
                candidate,
                title_id=1,
            )

        self.assertEqual(coordinate, (1, 2))

    def test_default_mapping_beats_alternate_candidate_coordinate(self) -> None:
        candidate = {
            "expected_episode_id": None,
            "order_namespace": "alternate",
            "season": 1,
            "episode": 8,
            "details": {
                "mappings": [
                    {
                        "order_namespace": "default",
                        "season": 1,
                        "episode": 4,
                    },
                    {
                        "order_namespace": "alternate",
                        "season": 1,
                        "episode": 8,
                    },
                ]
            },
        }
        with self.database.connect() as conn:
            coordinate = self.service._candidate_default_coordinate(
                conn,
                candidate,
            )

        self.assertEqual(coordinate, (1, 4))

    def test_conflicting_default_mappings_fail_closed(self) -> None:
        candidate = {
            "expected_episode_id": None,
            "order_namespace": "alternate",
            "season": 1,
            "episode": 8,
            "details": {
                "mappings": [
                    {
                        "order_namespace": "default",
                        "season": 1,
                        "episode": 4,
                    },
                    {
                        "order_namespace": "default",
                        "season": 1,
                        "episode": 5,
                    },
                ]
            },
        }
        with self.database.connect() as conn:
            coordinate = self.service._candidate_default_coordinate(
                conn,
                candidate,
            )

        self.assertIsNone(coordinate)

    def test_run_reports_missing_and_invalid_peer_scans_separately(self) -> None:
        hypotheses = {
            1: _hypothesis(1, 1, 2),
            2: _hypothesis(2, 2, 3),
            4: _hypothesis(
                4,
                4,
                4,
                state=IdentityResultState.VERIFIED,
            ),
        }

        self.service._validated_hypothesis = (
            lambda _conn, _scan_id: hypotheses[1]
        )

        def peer(_conn, file_id):
            if file_id == 2:
                return hypotheses[2], True
            if file_id == 3:
                return None, False
            if file_id == 4:
                return hypotheses[4], True
            raise AssertionError(file_id)

        self.service._best_current_hypothesis = peer

        with patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot_is_current",
            return_value=(True, {"title_id": 1}),
        ):
            result = self.service.run(
                self.scan.scan_id,
                self.interpretation,
            )

        self.assertEqual(result.missing_scan_file_ids, (3,))
        self.assertEqual(result.invalid_scan_file_ids, ())
        self.assertEqual(result.hypothesis_count, 3)
        self.assertEqual(
            {item.file_id for item in result.hypotheses},
            {1, 2, 4},
        )

    def test_invalid_peer_scan_is_not_treated_as_missing(self) -> None:
        hypotheses = {
            1: _hypothesis(1, 1, 2),
            2: _hypothesis(2, 2, 3),
            4: _hypothesis(
                4,
                4,
                4,
                state=IdentityResultState.VERIFIED,
            ),
        }
        self.service._validated_hypothesis = (
            lambda _conn, _scan_id: hypotheses[1]
        )

        def peer(_conn, file_id):
            if file_id == 2:
                return hypotheses[2], True
            if file_id == 3:
                return None, True
            if file_id == 4:
                return hypotheses[4], True
            raise AssertionError(file_id)

        self.service._best_current_hypothesis = peer

        with patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot_is_current",
            return_value=(True, {"title_id": 1}),
        ):
            result = self.service.run(
                self.scan.scan_id,
                self.interpretation,
            )

        self.assertEqual(result.missing_scan_file_ids, ())
        self.assertEqual(result.invalid_scan_file_ids, (3,))

    def test_invalid_target_hypothesis_aborts_sequence_run(self) -> None:
        self.service._validated_hypothesis = (
            lambda _conn, _scan_id: None
        )
        self.service._best_current_hypothesis = (
            lambda _conn, _file_id: (None, False)
        )

        with patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot_is_current",
            return_value=(True, {"title_id": 1}),
        ):
            with self.assertRaisesRegex(
                DeepSequenceCorrelationError,
                "target resolver snapshot is invalid",
            ):
                self.service.run(
                    self.scan.scan_id,
                    self.interpretation,
                )

    def test_sequence_run_rejects_overlapping_missing_and_invalid_files(self) -> None:
        from app.media_identity.sequence_correlation import (
            SequenceOffsetAnalysis,
            SequenceOffsetPolicy,
        )
        from app.media_identity.sequence_correlation_service import (
            DeepSequenceCorrelationRun,
        )

        target = _hypothesis(1, 1, 2)
        analysis = SequenceOffsetAnalysis(
            policy=SequenceOffsetPolicy(),
            hypothesis_count=1,
            usable_count=1,
            excluded_file_ids=(),
            observations=(),
            conflicted_seasons=(),
        )
        with self.assertRaisesRegex(
            DeepSequenceCorrelationError,
            "overlap",
        ):
            DeepSequenceCorrelationRun(
                scan_id=1,
                target_file_id=1,
                result_revision=1,
                correlation_plan_signature="a" * 64,
                sequence_policy_signature=hashlib.sha256(
                    json.dumps(
                        analysis.policy.identity_payload(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest(),
                sequence_policy_identity=analysis.policy.identity_payload(),
                planned_file_count=3,
                hypothesis_count=1,
                missing_scan_file_ids=(2,),
                invalid_scan_file_ids=(2,),
                hypotheses=(target,),
                analysis=analysis,
            )

    def test_target_revision_drift_is_rejected(self) -> None:
        stale = DeepCorrelationInterpretationRun(
            scan_id=self.interpretation.scan_id,
            result_revision=self.revision + 1,
            correlation_plan_signature=self.plan.plan_signature,
            interpretation_version=1,
            policy_signature=self.interpretation.policy_signature,
            policy_identity=self.interpretation.policy_identity,
            complete_modalities=(),
            planned_pair_count=len(self.plan.comparison_pairs),
            pairs=(),
        )

        with self.assertRaisesRegex(
            DeepSequenceCorrelationError,
            "publication changed",
        ):
            self.service.run(
                self.scan.scan_id,
                stale,
            )

    def test_stale_interpretation_version_is_rejected_at_handoff(self) -> None:
        from app.media_identity.correlation_interpretation import (
            CorrelationInterpretationError,
        )

        with self.assertRaisesRegex(
            CorrelationInterpretationError,
            "version is stale",
        ):
            DeepCorrelationInterpretationRun(
                scan_id=self.interpretation.scan_id,
                result_revision=self.revision,
                correlation_plan_signature=self.plan.plan_signature,
                interpretation_version=999,
                policy_signature=self.interpretation.policy_signature,
                policy_identity=self.interpretation.policy_identity,
                complete_modalities=(),
                planned_pair_count=len(self.plan.comparison_pairs),
                pairs=(),
            )

    def test_plan_signature_drift_is_rejected(self) -> None:
        stale = DeepCorrelationInterpretationRun(
            scan_id=self.interpretation.scan_id,
            result_revision=self.revision,
            correlation_plan_signature="b" * 64,
            interpretation_version=1,
            policy_signature=self.interpretation.policy_signature,
            policy_identity=self.interpretation.policy_identity,
            complete_modalities=(),
            planned_pair_count=len(self.plan.comparison_pairs),
            pairs=(),
        )

        with patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot_is_current",
            return_value=(True, {"title_id": 1}),
        ):
            with self.assertRaisesRegex(
                DeepSequenceCorrelationError,
                "cohort changed",
            ):
                self.service.run(
                    self.scan.scan_id,
                    stale,
                )

    def test_validated_hypothesis_rejects_resolver_state_drift(self) -> None:
        candidate = {
            "candidate_key": "candidate:1",
            "expected_episode_id": 2,
            "order_namespace": "default",
            "season": 1,
            "episode": 2,
            "details": {},
        }
        scan = {
            "id": 44,
            "file_id": 1,
            "status": "complete",
            "result_state": IdentityResultState.LIKELY_MISMATCH.value,
            "best_candidate_key": "candidate:1",
            "claimed_identity_json": json.dumps({
                "season": 1,
                "episode_start": 1,
                "episode_end": 1,
                "result_revision": 4,
            }),
        }
        resolved_candidate = CandidateResolution(
            candidate_key="candidate:1",
            score=0.80,
            support_strength=0.75,
            conflict_strength=0.10,
            independent_categories=2,
            support_groups=2,
            content_support=True,
            details={},
        )
        mismatched_resolution = IdentityResolution(
            state=IdentityResultState.STRONG_MATCH_OTHER,
            best_candidate_key="candidate:1",
            candidates=(resolved_candidate,),
            margin=0.25,
            claimed_candidate_keys=(),
            explanation="fixture",
        )

        with self.database.connect() as conn, patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot",
            return_value=(scan, [candidate], []),
        ), patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot_is_current",
            return_value=(True, {"title_id": 1}),
        ), patch.object(
            MediaIdentityDecisionService,
            "_decision_token",
            return_value=(4, "a" * 64),
        ), patch.object(
            MediaIdentityDecisionService,
            "_resolve_snapshot",
            return_value=mismatched_resolution,
        ):
            result = self.service._validated_hypothesis(
                conn,
                44,
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
