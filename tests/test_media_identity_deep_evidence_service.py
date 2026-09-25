from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.db import Database
from app.media_identity.decision_snapshot import (
    result_revision,
    seal_decision_snapshot,
)
from app.media_identity.deep_evidence_service import (
    DEEP_SPEECH_EVIDENCE_KEY,
    DEEP_VISUAL_EVIDENCE_KEY,
    DeepEvidencePromotionError,
    DeepEvidencePromotionService,
    _SpeechCorpus,
    _VisualCorpus,
)
from app.media_identity.models import (
    IdentityCandidate,
    IdentityReference,
    IdentityResultState,
)
from app.media_identity.normal_service import NORMAL_OCR_EVIDENCE_KEY
from app.media_identity.service import MediaIdentityDecisionService


class DeepEvidencePromotionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example"
        self.show_root.mkdir(parents=True)
        self.media_path = self.show_root / "Example - S01E01.mkv"
        self.media_path.write_bytes(b"deep-evidence-fixture" * 128)
        stat = self.media_path.stat()

        self.database = Database(self.root / "deep-evidence.db")
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
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,runtime_seconds,
                     width,height,video_codec,audio_codec,audio_channels,bitrate,
                     container,dynamic_range,media_info_at,media_info_error,
                     seen_scan
                   ) VALUES (
                     1,1,?,?,?,?,?,1,1,1,'Example',600,1920,1080,
                     'H264','AAC',2,5000000,'MKV','SDR',
                     '2026-09-24T12:00:00','','fixture'
                   )""",
                (
                    str(self.media_path),
                    self.media_path.name,
                    "mkv",
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,1,?,?,?,?)""",
                [
                    (1, 1001, 1, 1, "Episode One"),
                    (2, 1002, 1, 2, "Episode Two"),
                    (3, 1003, 1, 3, "Episode Three"),
                ],
            )

            self.base = self._candidate(
                provider_item_id="1001",
                expected_episode_id=1,
                episode=1,
                overview=(
                    "A rescue team repairs a failing restaurant and "
                    "rebuilds the kitchen."
                ),
                rank=1,
            )
            self.deep = self._candidate(
                provider_item_id="1002",
                expected_episode_id=2,
                episode=2,
                overview=(
                    "A storm damages a marina while the owners race "
                    "to save the boats."
                ),
                rank=2,
            )
            cursor = conn.execute(
                """INSERT INTO media_identity_scans(
                     file_id,identity_kind,requested_profile,completed_profile,
                     status,stage,claimed_identity_json,file_size_bytes,
                     file_modified_at,file_sha256,metadata_signature,
                     result_state,best_candidate_key,completed_at
                   ) VALUES (
                     1,'episode','normal','normal','complete','resolved',
                     ?,?,?,?,?,
                     ?,?,CURRENT_TIMESTAMP
                   )""",
                (
                    json.dumps({
                        "identity_kind": "episode",
                        "season": 1,
                        "episode_start": 1,
                        "episode_end": 1,
                        "scan_language": "eng",
                    }),
                    stat.st_size,
                    stat.st_mtime,
                    "a" * 64,
                    "fixture-metadata",
                    IdentityResultState.VERIFIED.value,
                    self.base.key,
                ),
            )
            self.scan_id = int(cursor.lastrowid)
            self._insert_candidate(conn, self.base)
            conn.execute(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,source_ref,value_text,details_json,
                     cache_key,profile
                   ) VALUES (
                     ?,?,'preview-ocr-synopsis','5','visual_text',
                     'visual-text:1:jellyfin','supports',0.40,
                     'external_preview_ocr','fixture','normal visual','{}',
                     'normal-visual','normal'
                   )""",
                (self.scan_id, self.base.key),
            )
            conn.execute(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,source_ref,value_text,details_json,
                     cache_key,profile
                   ) VALUES (
                     ?,?,'speech-synopsis','2','speech',
                     'subtitle-dialogue:1','supports',0.45,
                     'local_speech_transcript','fixture','normal speech','{}',
                     'normal-speech','normal'
                   )""",
                (self.scan_id, self.base.key),
            )
            seal_decision_snapshot(
                conn,
                self.scan_id,
                revision=5,
            )

        self.service = DeepEvidencePromotionService(
            self.database,
        )
        self.deep_identity = {
            "algorithm_version": 9,
            "candidate_policy": {
                "adjacent_season_radius": 1,
                "include_specials": True,
                "max_candidates": 160,
                "max_specials": 24,
            },
            "candidate_plan_signature": "c" * 64,
            "candidate_keys": [self.base.key, self.deep.key],
            "correlation_policy": {
                "season_radius": 0,
                "max_files": 48,
                "max_pairwise_comparisons": 256,
            },
            "correlation_plan_signature": "d" * 64,
            "correlation_file_ids": [1],
            "comparison_pairs": [],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _candidate(
        *,
        provider_item_id: str,
        expected_episode_id: int,
        episode: int,
        overview: str,
        rank: int,
    ) -> IdentityCandidate:
        return IdentityCandidate(
            identity=IdentityReference(
                identity_kind="episode",
                provider="tvdb",
                provider_item_id=provider_item_id,
                expected_episode_id=expected_episode_id,
                order_namespace="default",
                season=1,
                episode=episode,
                display_name=f"Episode {episode}",
            ),
            rank=rank,
            details={
                "overview": overview,
                "origins": ["same_season"],
                "mappings": [{
                    "order_namespace": "default",
                    "season": 1,
                    "episode": episode,
                }],
            },
        )

    def _insert_candidate(
        self,
        conn,
        candidate: IdentityCandidate,
    ) -> None:
        identity = candidate.identity
        conn.execute(
            """INSERT INTO media_identity_candidates(
                 scan_id,candidate_key,identity_kind,provider,
                 provider_item_id,expected_episode_id,order_namespace,
                 season,episode,display_name,rank,score,support_strength,
                 conflict_strength,independent_categories,details_json
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,0,0,0,?)""",
            (
                self.scan_id,
                candidate.key,
                identity.identity_kind,
                identity.provider,
                identity.provider_item_id,
                identity.expected_episode_id,
                identity.order_namespace,
                identity.season,
                identity.episode,
                identity.display_name,
                candidate.rank,
                json.dumps(candidate.details, sort_keys=True),
            ),
        )

    def _plan(
        self,
        candidates: tuple[IdentityCandidate, ...],
    ):
        keys = [item.key for item in candidates]
        identity = {
            **self.deep_identity,
            "candidate_keys": keys,
        }
        return (
            SimpleNamespace(
                candidates=candidates,
                plan_signature=identity[
                    "candidate_plan_signature"
                ],
            ),
            SimpleNamespace(
                plan_signature=identity[
                    "correlation_plan_signature"
                ],
            ),
            identity,
        )

    @staticmethod
    def _visual_corpus() -> _VisualCorpus:
        return _VisualCorpus(
            manifest_id=101,
            texts=(
                "storm damages marina owners save boats",
                "marina boats storm damage",
            ),
            confidences=(0.90, 0.80),
            artifact_ids=(201, 202),
            timestamps_ms=(120_000, 360_000),
            plan_signature="v" * 64,
        )

    @staticmethod
    def _speech_corpus() -> _SpeechCorpus:
        return _SpeechCorpus(
            manifest_id=102,
            texts=(
                "storm damaged the marina and we need to save the boats",
                "owners rush to protect the boats",
            ),
            languages=("eng", "eng"),
            artifact_ids=(301, 302),
            windows=((90_000, 120_000), (390_000, 420_000)),
            plan_signature="s" * 64,
            synopsis_language="eng",
        )

    def _current_patch(self):
        return patch.object(
            MediaIdentityDecisionService,
            "_scan_snapshot_is_current",
            side_effect=lambda _conn, _scan, _evidence: (
                True,
                {
                    "id": 1,
                    "title_id": 1,
                    "path": str(self.media_path),
                },
            ),
        )

    def test_candidate_only_promotion_widens_without_claiming_deep_complete(self) -> None:
        self.service._deep_plan = (
            lambda _conn, _scan, _file: self._plan(
                (self.base, self.deep)
            )
        )

        with self._current_patch():
            result = self.service.promote(
                self.scan_id
            )

        self.assertEqual(result.baseline_revision, 5)
        self.assertEqual(result.staged_revision, 6)
        self.assertEqual(result.added_candidate_count, 1)
        self.assertEqual(result.evidence_count, 0)

        with self.database.connect() as conn:
            scan = dict(conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone())
            candidates = conn.execute(
                """SELECT candidate_key FROM media_identity_candidates
                   WHERE scan_id=? ORDER BY candidate_key""",
                (self.scan_id,),
            ).fetchall()
        claimed = json.loads(scan["claimed_identity_json"])
        self.assertEqual(scan["requested_profile"], "deep")
        self.assertEqual(scan["completed_profile"], "normal")
        self.assertEqual(scan["stage"], "deep_evidence_staged")
        self.assertIsNone(scan["result_state"])
        self.assertIsNone(scan["best_candidate_key"])
        self.assertEqual(result_revision(scan), 6)
        self.assertEqual(
            {row["candidate_key"] for row in candidates},
            {self.base.key, self.deep.key},
        )
        self.assertEqual(
            claimed["deep_evidence"]["added_candidate_keys"],
            [self.deep.key],
        )
        self.assertIsNone(
            claimed["deep_evidence"][
                "visual_manifest_artifact_id"
            ]
        )
        self.assertIsNone(
            claimed["deep_evidence"][
                "speech_manifest_artifact_id"
            ]
        )

    def test_promoted_text_reuses_normal_correlation_groups(self) -> None:
        self.service._deep_plan = (
            lambda _conn, _scan, _file: self._plan(
                (self.base, self.deep)
            )
        )
        visual = SimpleNamespace(coverage_complete=True)
        speech = SimpleNamespace(coverage_complete=True)

        with self._current_patch(), patch.object(
            self.service,
            "_validate_visual_manifest",
            return_value=self._visual_corpus(),
        ), patch.object(
            self.service,
            "_validate_speech_manifest",
            return_value=self._speech_corpus(),
        ):
            result = self.service.promote(
                self.scan_id,
                visual=visual,
                speech=speech,
            )

        self.assertGreater(result.visual_evidence_count, 0)
        self.assertGreater(result.speech_evidence_count, 0)
        with self.database.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """SELECT analyzer_key,correlation_group,profile,
                              candidate_key,relation,strength
                       FROM media_identity_evidence
                       WHERE scan_id=?
                         AND analyzer_key IN (?,?)
                       ORDER BY analyzer_key,candidate_key""",
                    (
                        self.scan_id,
                        DEEP_VISUAL_EVIDENCE_KEY,
                        DEEP_SPEECH_EVIDENCE_KEY,
                    ),
                ).fetchall()
            ]
            normal = [
                dict(row)
                for row in conn.execute(
                    """SELECT analyzer_key,correlation_group
                       FROM media_identity_evidence
                       WHERE scan_id=?
                         AND analyzer_key IN (
                           'preview-ocr-synopsis','speech-synopsis'
                         )
                       ORDER BY analyzer_key""",
                    (self.scan_id,),
                ).fetchall()
            ]
        self.assertTrue(rows)
        self.assertTrue(
            all(row["profile"] == "deep" for row in rows)
        )
        visual_groups = {
            row["correlation_group"]
            for row in rows
            if row["analyzer_key"] == DEEP_VISUAL_EVIDENCE_KEY
        }
        speech_groups = {
            row["correlation_group"]
            for row in rows
            if row["analyzer_key"] == DEEP_SPEECH_EVIDENCE_KEY
        }
        self.assertEqual(
            visual_groups,
            {"visual-text:1:jellyfin"},
        )
        self.assertEqual(
            speech_groups,
            {"subtitle-dialogue:1"},
        )
        self.assertEqual(
            {
                row["analyzer_key"] for row in normal
            },
            {"preview-ocr-synopsis", "speech-synopsis"},
        )
        self.assertTrue(
            any(
                row["candidate_key"] == self.deep.key
                and row["relation"] == "supports"
                for row in rows
            )
        )

    def test_rerun_removes_stale_deep_modality_and_deep_only_candidate(self) -> None:
        plans = iter([
            self._plan((self.base, self.deep)),
            self._plan((self.base,)),
        ])
        self.service._deep_plan = (
            lambda _conn, _scan, _file: next(plans)
        )
        visual = SimpleNamespace(coverage_complete=True)
        speech = SimpleNamespace(coverage_complete=True)

        with self._current_patch(), patch.object(
            self.service,
            "_validate_visual_manifest",
            return_value=self._visual_corpus(),
        ), patch.object(
            self.service,
            "_validate_speech_manifest",
            return_value=self._speech_corpus(),
        ):
            self.service.promote(
                self.scan_id,
                visual=visual,
                speech=speech,
            )
            second = self.service.promote(
                self.scan_id,
            )

        self.assertEqual(second.added_candidate_count, 0)
        with self.database.connect() as conn:
            candidate_keys = {
                row["candidate_key"]
                for row in conn.execute(
                    """SELECT candidate_key
                       FROM media_identity_candidates
                       WHERE scan_id=?""",
                    (self.scan_id,),
                ).fetchall()
            }
            deep_evidence_count = conn.execute(
                """SELECT COUNT(*)
                   FROM media_identity_evidence
                   WHERE scan_id=?
                     AND analyzer_key IN (?,?)""",
                (
                    self.scan_id,
                    DEEP_VISUAL_EVIDENCE_KEY,
                    DEEP_SPEECH_EVIDENCE_KEY,
                ),
            ).fetchone()[0]
            scan = dict(conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone())
        claimed = json.loads(scan["claimed_identity_json"])
        self.assertEqual(candidate_keys, {self.base.key})
        self.assertEqual(deep_evidence_count, 0)
        self.assertEqual(
            claimed["deep_evidence"]["added_candidate_keys"],
            [],
        )
        self.assertEqual(result_revision(scan), 7)

    def test_deep_plan_cannot_narrow_normal_baseline_candidates(self) -> None:
        second_base = self._candidate(
            provider_item_id="1003",
            expected_episode_id=3,
            episode=3,
            overview="A third baseline episode.",
            rank=2,
        )
        with self.database.connect() as conn:
            self._insert_candidate(conn, second_base)
            seal_decision_snapshot(
                conn,
                self.scan_id,
                revision=6,
            )
        self.service._deep_plan = (
            lambda _conn, _scan, _file: self._plan(
                (self.base,)
            )
        )

        with self._current_patch(), self.assertRaisesRegex(
            DeepEvidencePromotionError,
            "would narrow",
        ):
            self.service.promote(
                self.scan_id,
            )

        with self.database.connect() as conn:
            scan = dict(conn.execute(
                "SELECT * FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone())
            candidate_keys = {
                row["candidate_key"]
                for row in conn.execute(
                    """SELECT candidate_key
                       FROM media_identity_candidates
                       WHERE scan_id=?""",
                    (self.scan_id,),
                ).fetchall()
            }
        self.assertEqual(
            candidate_keys,
            {self.base.key, second_base.key},
        )
        self.assertEqual(scan["stage"], "resolved")
        self.assertEqual(result_revision(scan), 6)


if __name__ == "__main__":
    unittest.main()
