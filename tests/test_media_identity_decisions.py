from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.models import IdentityReference, IdentityResultState
from app.media_identity.scoring import resolve_identity
from app.media_identity.service import MediaIdentityDecisionService
from app.mie_history import MediaIntelligenceHistoryEngine


def _candidate(
    key: str,
    *,
    claimed: bool = False,
    episode: int = 1,
    mappings: list[dict] | None = None,
) -> dict:
    return {
        "candidate_key": key,
        "identity_kind": "episode",
        "provider": "tvdb",
        "provider_item_id": key,
        "expected_episode_id": episode,
        "order_namespace": "default",
        "season": 1,
        "episode": episode,
        "display_name": f"Episode {episode}",
        "rank": episode,
        "details": {
            "origins": ["claimed_coordinate", "same_season"] if claimed else ["same_season"],
            "mappings": mappings or [
                {
                    "order_namespace": "default",
                    "order_name": "Default",
                    "season": 1,
                    "episode": episode,
                }
            ],
        },
    }


def _evidence(
    key: str,
    category: str,
    strength: float,
    group: str,
    *,
    relation: str = "supports",
) -> dict:
    return {
        "candidate_key": key,
        "evidence_category": category,
        "relation": relation,
        "strength": strength,
        "correlation_group": group,
    }


class ConservativeResolverTests(unittest.TestCase):
    CLAIM = {
        "identity_kind": "episode",
        "season": 1,
        "episode_start": 1,
        "episode_end": 1,
    }

    def test_claimed_filename_evidence_alone_is_inconclusive(self) -> None:
        resolution = resolve_identity(
            [_candidate("claimed", claimed=True)],
            [_evidence("claimed", "claimed_identity", 0.35, "catalog-claim")],
            self.CLAIM,
        )
        self.assertEqual(resolution.state, IdentityResultState.INCONCLUSIVE)

    def test_correlated_evidence_counts_once(self) -> None:
        resolution = resolve_identity(
            [_candidate("claimed", claimed=True)],
            [
                _evidence("claimed", "claimed_identity", 0.35, "same-dialogue"),
                _evidence("claimed", "provider_metadata", 0.70, "same-dialogue"),
            ],
            self.CLAIM,
        )
        candidate = resolution.candidates[0]
        self.assertEqual(candidate.support_groups, 1)
        self.assertAlmostEqual(candidate.support_strength, 0.70)
        self.assertEqual(resolution.state, IdentityResultState.INCONCLUSIVE)

    def test_three_independent_signals_can_verify_claimed_episode(self) -> None:
        resolution = resolve_identity(
            [
                _candidate("claimed", claimed=True),
                _candidate("other", episode=2),
            ],
            [
                _evidence("claimed", "claimed_identity", 0.35, "claim"),
                _evidence("claimed", "container_metadata", 0.18, "runtime"),
                _evidence("claimed", "subtitle_text", 0.80, "dialogue"),
                _evidence("other", "container_metadata", 0.18, "runtime"),
                _evidence("other", "subtitle_text", 0.20, "dialogue"),
            ],
            self.CLAIM,
        )
        self.assertEqual(resolution.state, IdentityResultState.VERIFIED)
        self.assertEqual(resolution.best_candidate_key, "claimed")

    def test_near_tie_stays_inconclusive_even_with_strong_content(self) -> None:
        resolution = resolve_identity(
            [
                _candidate("claimed", claimed=True),
                _candidate("other", episode=2),
            ],
            [
                _evidence("claimed", "claimed_identity", 0.35, "claim"),
                _evidence("claimed", "subtitle_text", 0.78, "dialogue"),
                _evidence("claimed", "container_metadata", 0.18, "runtime"),
                _evidence("other", "subtitle_text", 0.80, "dialogue"),
                _evidence("other", "container_metadata", 0.18, "runtime"),
            ],
            self.CLAIM,
        )
        self.assertEqual(resolution.state, IdentityResultState.INCONCLUSIVE)

    def test_strong_different_episode_requires_content_plus_independent_support(self) -> None:
        resolution = resolve_identity(
            [
                _candidate("claimed", claimed=True),
                _candidate("other", episode=2),
            ],
            [
                _evidence("claimed", "claimed_identity", 0.35, "claim"),
                _evidence("claimed", "container_metadata", 0.18, "runtime"),
                _evidence("other", "subtitle_text", 0.86, "dialogue"),
                _evidence("other", "container_metadata", 0.30, "runtime"),
            ],
            self.CLAIM,
        )
        self.assertEqual(resolution.state, IdentityResultState.STRONG_MATCH_OTHER)
        self.assertEqual(resolution.best_candidate_key, "other")

    def test_alternate_order_is_not_content_mismatch(self) -> None:
        candidate = _candidate(
            "same-content",
            claimed=True,
            episode=1,
            mappings=[
                {
                    "order_namespace": "production",
                    "order_name": "Production",
                    "season": 1,
                    "episode": 1,
                },
                {
                    "order_namespace": "default",
                    "order_name": "Default",
                    "season": 1,
                    "episode": 4,
                },
            ],
        )
        resolution = resolve_identity(
            [candidate],
            [
                _evidence("same-content", "claimed_identity", 0.35, "claim"),
                _evidence("same-content", "container_metadata", 0.18, "runtime"),
                _evidence("same-content", "subtitle_text", 0.90, "dialogue"),
            ],
            self.CLAIM,
        )
        self.assertEqual(
            resolution.state, IdentityResultState.EPISODE_ORDER_CONFLICT
        )

    def test_correct_heavy_cohort_produces_no_mismatch_states(self) -> None:
        mismatch_states = {
            IdentityResultState.POSSIBLE_MISMATCH,
            IdentityResultState.LIKELY_MISMATCH,
            IdentityResultState.STRONG_MATCH_OTHER,
        }
        for episode in range(1, 13):
            claim = {
                "identity_kind": "episode",
                "season": 1,
                "episode_start": episode,
                "episode_end": episode,
            }
            claimed = _candidate(
                f"claimed-{episode}", claimed=True, episode=episode
            )
            neighbor = _candidate(
                f"neighbor-{episode}", episode=episode + 1
            )
            resolution = resolve_identity(
                [claimed, neighbor],
                [
                    _evidence(
                        claimed["candidate_key"], "claimed_identity", 0.35, "claim"
                    ),
                    _evidence(
                        claimed["candidate_key"], "container_metadata", 0.18, "runtime"
                    ),
                    _evidence(
                        claimed["candidate_key"], "subtitle_text", 0.70, "dialogue"
                    ),
                    _evidence(
                        neighbor["candidate_key"], "container_metadata", 0.18, "runtime"
                    ),
                    _evidence(
                        neighbor["candidate_key"], "subtitle_text", 0.18, "dialogue"
                    ),
                ],
                claim,
            )
            self.assertNotIn(resolution.state, mismatch_states)


class DecisionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example Show"
        self.show_root.mkdir(parents=True)
        self.media = self.show_root / "Example Show - S01E01.mkv"
        self.media.write_bytes(b"fixture media" * 20)
        stat_result = self.media.stat()

        self.database = Database(self.root / "catalog.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,?,'tv','TV')",
                (str(self.media_root),),
            )
            conn.execute(
                """INSERT INTO titles(
                     id,root_id,kind,title,metadata_title,metadata_year,folder_path,tvdb_id
                   ) VALUES (1,1,'tv','Example Show','Example Show',2026,?,4242)""",
                (str(self.show_root),),
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,1,?,1,?,?)""",
                [
                    (1, 1001, 1, "Pilot"),
                    (2, 1002, 2, "Second Story"),
                ],
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,seen_scan
                   ) VALUES (1,1,?,?,?,?,?,1,1,1,'Example Show','scan')""",
                (
                    str(self.media),
                    self.media.name,
                    "mkv",
                    stat_result.st_size,
                    stat_result.st_mtime,
                ),
            )
        self.service = MediaIdentityDecisionService(self.database)
        self.scan_id = self._insert_mismatch_scan(stat_result)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _insert_mismatch_scan(self, stat_result) -> int:
        claimed_ref = IdentityReference(
            "episode", "tvdb", "1001", expected_episode_id=1,
            order_namespace="default", season=1, episode=1, display_name="Pilot",
        )
        other_ref = IdentityReference(
            "episode", "tvdb", "1002", expected_episode_id=2,
            order_namespace="default", season=1, episode=2,
            display_name="Second Story",
        )
        with self.database.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO media_identity_scans(
                     file_id,identity_kind,requested_profile,completed_profile,status,
                     stage,claimed_identity_json,file_size_bytes,file_modified_at,
                     metadata_signature,completed_at
                   ) VALUES (1,'episode','fast','fast','complete','fast_complete',
                             ?,?,?, 'fixture-signature',CURRENT_TIMESTAMP)""",
                (
                    json.dumps({
                        "identity_kind": "episode",
                        "season": 1,
                        "episode_start": 1,
                        "episode_end": 1,
                        "filename": self.media.name,
                    }),
                    stat_result.st_size,
                    stat_result.st_mtime,
                ),
            )
            scan_id = int(cursor.lastrowid)
            rows = [
                (
                    scan_id, claimed_ref.content_key, "1001", 1, 1, "Pilot",
                    json.dumps({
                        "origins": ["claimed_coordinate", "same_season"],
                        "mappings": [{
                            "order_namespace": "default",
                            "order_name": "Default",
                            "season": 1,
                            "episode": 1,
                        }],
                    }),
                ),
                (
                    scan_id, other_ref.content_key, "1002", 2, 2, "Second Story",
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
            ]
            conn.executemany(
                """INSERT INTO media_identity_candidates(
                     scan_id,candidate_key,identity_kind,provider,provider_item_id,
                     expected_episode_id,order_namespace,season,episode,display_name,
                     rank,details_json
                   ) VALUES (?,?,'episode','tvdb',?,?,'default',1,?,?,1,?)""",
                rows,
            )
            conn.executemany(
                """INSERT INTO media_identity_evidence(
                     scan_id,candidate_key,analyzer_key,analyzer_version,
                     evidence_category,correlation_group,relation,strength,
                     source_kind,profile
                   ) VALUES (?,?,'fixture','1',?,?, 'supports',?,'fixture','fast')""",
                [
                    (
                        scan_id, claimed_ref.content_key,
                        "claimed_identity", "claim", 0.35,
                    ),
                    (
                        scan_id, claimed_ref.content_key,
                        "container_metadata", "runtime", 0.18,
                    ),
                    (
                        scan_id, other_ref.content_key,
                        "subtitle_text", "dialogue", 0.88,
                    ),
                    (
                        scan_id, other_ref.content_key,
                        "container_metadata", "runtime", 0.30,
                    ),
                ],
            )
        return scan_id

    def test_resolve_scan_persists_scores_and_result(self) -> None:
        result = self.service.resolve_scan(self.scan_id)
        self.assertEqual(result.state, IdentityResultState.STRONG_MATCH_OTHER)
        with self.database.connect() as conn:
            scan = conn.execute(
                "SELECT result_state,best_candidate_key,stage FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
            best = conn.execute(
                """SELECT score,support_strength,independent_categories,details_json
                   FROM media_identity_candidates
                   WHERE scan_id=? AND candidate_key=?""",
                (self.scan_id, result.best_candidate_key),
            ).fetchone()
        self.assertEqual(scan["result_state"], "strong_match_other")
        self.assertEqual(scan["stage"], "resolved")
        self.assertGreater(float(best["support_strength"]), 0.8)
        self.assertGreaterEqual(int(best["independent_categories"]), 2)
        self.assertIn("resolution", json.loads(best["details_json"]))

    def test_mie_history_resolves_pending_scan_and_persists_advisory(self) -> None:
        mie = MediaIntelligenceHistoryEngine(self.database)
        mie.analyze()
        with self.database.connect() as conn:
            scan = conn.execute(
                "SELECT result_state,best_candidate_key FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
            finding = conn.execute(
                """SELECT rule_key,file_id,evidence_json,status
                   FROM mie_findings
                   WHERE rule_key='episode-identity-review' AND file_id=1"""
            ).fetchone()
        self.assertEqual(scan["result_state"], "strong_match_other")
        self.assertTrue(scan["best_candidate_key"])
        self.assertIsNotNone(finding)
        self.assertEqual(finding["status"], "active")
        evidence = json.loads(finding["evidence_json"])
        self.assertEqual(evidence["scan_id"], self.scan_id)
        self.assertEqual(evidence["result_state"], "strong_match_other")
        self.assertNotIn("candidate_margin", evidence)
        self.assertIn("candidate_separation", evidence)

    def test_confirmation_is_snapshot_bound_and_becomes_stale(self) -> None:
        self.service.resolve_scan(self.scan_id)
        confirmation = self.service.confirm_current(self.scan_id, None)
        self.assertTrue(confirmation["current"])

        original = self.media.stat()
        self.media.write_bytes(b"X" * original.st_size)
        os.utime(
            self.media,
            ns=(original.st_atime_ns, original.st_mtime_ns + 1_000_000),
        )
        stale = self.service.confirmation_status(1)
        self.assertIsNotNone(stale)
        self.assertFalse(stale["current"])
        self.assertEqual(stale["freshness"], "stale")

    def test_mark_correct_suppresses_advisory_finding_only_for_current_snapshot(self) -> None:
        self.service.resolve_scan(self.scan_id)
        self.assertEqual(len(self.service.mie_findings()), 1)
        self.service.confirm_current(self.scan_id, None)
        self.assertEqual(self.service.mie_findings(), [])

    def test_confirm_best_rejects_an_inconclusive_result(self) -> None:
        self.service.resolve_scan(self.scan_id)
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_identity_scans
                   SET result_state='inconclusive'
                   WHERE id=?""",
                (self.scan_id,),
            )
        with self.assertRaisesRegex(
            ValueError, "not strong enough to confirm an alternate episode"
        ):
            self.service.confirm_best(self.scan_id, None)

    def test_confirm_best_keeps_reviewable_filename_disagreement(self) -> None:
        self.service.resolve_scan(self.scan_id)
        confirmation = self.service.confirm_best(self.scan_id, None)
        self.assertTrue(confirmation["current"])
        findings = self.service.mie_findings()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_key"], "episode-identity-review")

    def test_rename_preview_is_read_only_and_targets_best_candidate(self) -> None:
        self.service.resolve_scan(self.scan_id)
        before = self.media.read_bytes()
        preview = self.service.rename_preview(self.scan_id)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["target_episode"], 2)
        self.assertIn("S01E02", Path(preview["destination"]).name)
        self.assertTrue(self.media.exists())
        self.assertEqual(self.media.read_bytes(), before)
        with self.database.connect() as conn:
            file_row = conn.execute(
                "SELECT path,season,episode_start FROM files WHERE id=1"
            ).fetchone()
        self.assertEqual(file_row["path"], str(self.media))
        self.assertEqual(file_row["episode_start"], 1)

    def test_file_change_blocks_confirmation(self) -> None:
        self.service.resolve_scan(self.scan_id)
        self.media.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            self.service.confirm_current(self.scan_id, None)


if __name__ == "__main__":
    unittest.main()
