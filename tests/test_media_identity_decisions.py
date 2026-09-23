from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.candidates import generate_episode_candidates
from app.media_identity.decision_snapshot import (
    DecisionSnapshotError,
    result_revision,
    seal_decision_snapshot,
)
from app.media_identity.fast import (
    SCAN_INPUT_SIGNATURE_VERSION,
    FastIdentityService,
    combined_scan_input_signature,
    scan_input_signatures,
)
from app.media_identity.models import IdentityReference, IdentityResultState
from app.media_identity.scoring import resolve_identity
from app.media_identity.service import MediaIdentityDecisionService
from app.media_identity.versions import (
    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION,
)
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

    def test_subtitle_and_speech_dialogue_count_as_one_support_group(self) -> None:
        resolution = resolve_identity(
            [_candidate("claimed", claimed=True)],
            [
                _evidence("claimed", "subtitle_text", 0.42, "subtitle-dialogue:1"),
                _evidence("claimed", "speech", 0.78, "subtitle-dialogue:1"),
            ],
            self.CLAIM,
        )
        candidate = resolution.candidates[0]
        self.assertEqual(candidate.support_groups, 1)
        self.assertAlmostEqual(candidate.support_strength, 0.78)
        self.assertEqual(candidate.independent_categories, 1)
        self.assertEqual(
            candidate.details["correlation_groups"],
            ["subtitle-dialogue:1"],
        )

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

    def test_alternate_order_without_content_evidence_stays_inconclusive(self) -> None:
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
            [_evidence("same-content", "claimed_identity", 0.35, "claim")],
            self.CLAIM,
        )
        self.assertEqual(resolution.state, IdentityResultState.INCONCLUSIVE)

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

    def test_alternate_order_near_tie_is_inconclusive_before_actionable_state(self) -> None:
        claimed = _candidate(
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
        other = _candidate("other", episode=2)
        resolution = resolve_identity(
            [claimed, other],
            [
                _evidence("same-content", "claimed_identity", 0.35, "claim"),
                _evidence("same-content", "container_metadata", 0.18, "runtime"),
                _evidence("same-content", "subtitle_text", 0.90, "dialogue"),
                _evidence("other", "container_metadata", 0.18, "runtime"),
                _evidence("other", "subtitle_text", 0.89, "dialogue"),
            ],
            self.CLAIM,
        )
        self.assertEqual(resolution.best_candidate_key, "same-content")
        self.assertLess(resolution.margin, 0.12)
        self.assertEqual(resolution.state, IdentityResultState.INCONCLUSIVE)

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
            file_row = FastIdentityService._file_row(conn, 1)
            streams = FastIdentityService._stream_rows(conn, 1)
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
            claimed_identity = {
                "identity_kind": "episode",
                "season": 1,
                "episode_start": 1,
                "episode_end": 1,
                "filename": self.media.name,
                "scan_language": "eng",
                "expanded_specials": False,
                "input_signature_version": SCAN_INPUT_SIGNATURE_VERSION,
                "input_signatures": signatures,
                "decision_algorithm_version": (
                    EPISODE_IDENTITY_DECISION_ALGORITHM_VERSION
                ),
            }
            conn.execute(
                """UPDATE media_identity_scans
                   SET claimed_identity_json=?,metadata_signature=?
                   WHERE id=?""",
                (
                    json.dumps(claimed_identity, sort_keys=True),
                    combined_scan_input_signature(signatures),
                    scan_id,
                ),
            )
            seal_decision_snapshot(conn, scan_id, revision=1)
        return scan_id

    def _seed_real_fast_mismatch_inputs(self) -> Path:
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE files
                   SET runtime_seconds=1440,media_info_at='2026-09-18T12:00:00'
                   WHERE id=1"""
            )
            conn.execute(
                """INSERT INTO provider_episode_series_cache(
                     provider,provider_series_id,language,source_signature,
                     episode_count,mapping_count,order_namespaces_json
                   ) VALUES ('tvdb','4242','eng','provider-v1',2,2,
                             '[{"namespace":"default"}]')"""
            )
            conn.executemany(
                """INSERT INTO provider_episode_identities(
                     provider,provider_series_id,provider_episode_id,language,
                     name,overview,aired,metadata_json
                   ) VALUES ('tvdb','4242',?,'eng',?,?,?,?)""",
                [
                    (
                        "1001",
                        "Pilot",
                        "amber falcon orchard glacier velvet compass",
                        "2026-01-01",
                        json.dumps({"runtime": 24}),
                    ),
                    (
                        "1002",
                        "Second Story",
                        "bronze harbor lantern meadow quartz thunder",
                        "2026-01-08",
                        json.dumps({"runtime": 24}),
                    ),
                ],
            )
            conn.executemany(
                """INSERT INTO provider_episode_mappings(
                     provider,provider_series_id,provider_episode_id,language,
                     order_namespace,order_name,season,episode,absolute_number,
                     coordinate_key,details_json
                   ) VALUES ('tvdb','4242',?,'eng','default','Default',1,?,?,?,'{}')""",
                [
                    ("1001", 1, 1, json.dumps([1, 1, 1])),
                    ("1002", 2, 2, json.dumps([1, 2, 2])),
                ],
            )
        sidecar = self.media.with_suffix(".en.srt")
        sidecar.write_text(
            "1\n00:00:00,000 --> 00:00:05,000\n"
            "bronze harbor lantern meadow quartz thunder\n\n"
            "2\n00:00:06,000 --> 00:00:10,000\n"
            "bronze harbor lantern meadow quartz thunder\n",
            encoding="utf-8",
        )
        return sidecar

    def test_rename_preview_does_not_resolve_a_completed_pending_scan(self) -> None:
        preview = self.service.rename_preview(self.scan_id)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["status"], "unavailable")
        self.assertTrue(preview["scan"]["decision_pending"])
        with self.database.connect() as conn:
            scan = conn.execute(
                "SELECT result_state,best_candidate_key,stage FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
        self.assertIsNone(scan["result_state"])
        self.assertIsNone(scan["best_candidate_key"])
        self.assertEqual(scan["stage"], "fast_complete")

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

    def test_stale_scan_does_not_emit_mie_finding(self) -> None:
        self.service.resolve_scan(self.scan_id)
        self.assertEqual(len(self.service.mie_findings()), 1)

        self.media.write_bytes(b"replacement media with different contents")
        current = self.media.stat()
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE files SET size_bytes=?,modified_at=?
                   WHERE id=1""",
                (current.st_size, current.st_mtime),
            )

        detail = self.service.scan_detail(self.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        self.assertEqual(self.service.mie_findings(), [])

    def test_snapshot_sha_does_not_trust_or_require_cached_hash_record(self) -> None:
        self.service.resolve_scan(self.scan_id)
        digest = hashlib.sha256(self.media.read_bytes()).hexdigest()
        current = self.media.stat()
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_identity_scans SET file_sha256=?
                   WHERE id=?""",
                (digest, self.scan_id),
            )
            conn.execute(
                """INSERT INTO media_file_hashes(
                     file_id,sha256,size_bytes,modified_at,status,hashed_at
                   ) VALUES (1,?,?,?,'complete',CURRENT_TIMESTAMP)""",
                (digest, current.st_size, current.st_mtime),
            )
            scan = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
            seal_decision_snapshot(
                conn,
                self.scan_id,
                revision=result_revision(
                    {"claimed_identity_json": scan["claimed_identity_json"]}
                ) + 1,
            )

        self.assertTrue(self.service.scan_detail(self.scan_id)["snapshot_current"])
        with self.database.connect() as conn:
            conn.execute("DELETE FROM media_file_hashes WHERE file_id=1")
        self.assertTrue(self.service.scan_detail(self.scan_id)["snapshot_current"])

        original = self.media.stat()
        payload = bytearray(self.media.read_bytes())
        payload[0] ^= 0x01
        self.media.write_bytes(bytes(payload))
        os.utime(
            self.media,
            ns=(original.st_atime_ns, original.st_mtime_ns),
        )
        self.assertFalse(self.service.scan_detail(self.scan_id)["snapshot_current"])

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
        detail = self.service.scan_detail(self.scan_id)
        self.assertTrue(detail["confirmed_claimed"])
        self.assertFalse(detail["actionable"])
        preview = self.service.rename_preview(self.scan_id)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["status"], "unavailable")

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
        self.assertEqual(Path(preview["destination"]).suffix, ".mkv")
        self.assertTrue(self.media.exists())
        self.assertEqual(self.media.read_bytes(), before)
        with self.database.connect() as conn:
            file_row = conn.execute(
                "SELECT path,season,episode_start FROM files WHERE id=1"
            ).fetchone()
        self.assertEqual(file_row["path"], str(self.media))
        self.assertEqual(file_row["episode_start"], 1)

    def test_real_fast_scan_flows_through_decision_mie_and_sidecar_freshness(self) -> None:
        sidecar = self._seed_real_fast_mismatch_inputs()
        fast = FastIdentityService(self.database)

        scan = fast.scan_file(1)
        resolution = self.service.resolve_scan(scan.scan_id)

        self.assertEqual(resolution.state, IdentityResultState.STRONG_MATCH_OTHER)
        detail = self.service.scan_detail(scan.scan_id)
        self.assertTrue(detail["snapshot_current"])
        self.assertTrue(detail["actionable"])
        findings = self.service.mie_findings()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["evidence"]["scan_id"], scan.scan_id)
        self.assertEqual(findings[0]["evidence"]["result_state"], "strong_match_other")
        preview = self.service.rename_preview(scan.scan_id)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["target_episode"], 2)

        sidecar.write_text(
            "1\n00:00:00,000 --> 00:00:05,000\n"
            "replacement subtitle evidence that no longer matches the scan\n",
            encoding="utf-8",
        )

        stale = self.service.scan_detail(scan.scan_id)
        self.assertFalse(stale["snapshot_current"])
        self.assertFalse(stale["actionable"])
        self.assertEqual(self.service.mie_findings(), [])
        with self.assertRaisesRegex(
            ValueError, "changed after this identity scan"
        ):
            self.service.confirm_best(scan.scan_id, None)
        stale_preview = self.service.rename_preview(scan.scan_id)
        self.assertFalse(stale_preview["available"])
        self.assertEqual(stale_preview["status"], "stale")

    def _resolved_real_fast_scan(self):
        sidecar = self._seed_real_fast_mismatch_inputs()
        scan = FastIdentityService(self.database).scan_file(1)
        self.service.resolve_scan(scan.scan_id)
        self.assertTrue(self.service.scan_detail(scan.scan_id)["snapshot_current"])
        return scan, sidecar

    def test_same_size_same_mtime_media_edit_invalidates_actionable_scan(self) -> None:
        scan, _ = self._resolved_real_fast_scan()
        before = self.media.stat()
        original = self.media.read_bytes()
        replacement = bytes(
            (value ^ 0x01) if index == 0 else value
            for index, value in enumerate(original)
        )
        self.assertEqual(len(replacement), len(original))
        self.media.write_bytes(replacement)
        os.utime(
            self.media,
            ns=(before.st_atime_ns, before.st_mtime_ns),
        )

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        self.assertEqual(
            self.service.rename_preview(scan.scan_id)["status"],
            "stale",
        )
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            self.service.confirm_best(scan.scan_id, None)

    def test_equal_size_equal_mtime_sidecar_edit_invalidates_scan(self) -> None:
        scan, sidecar = self._resolved_real_fast_scan()
        before = sidecar.stat()
        original = sidecar.read_bytes()
        replacement = bytearray(original)
        replacement[-2] = (
            ord("x") if replacement[-2] != ord("x") else ord("y")
        )
        sidecar.write_bytes(bytes(replacement))
        os.utime(
            sidecar,
            ns=(before.st_atime_ns, before.st_mtime_ns),
        )

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_provider_snapshot_change_invalidates_action_and_confirmation(self) -> None:
        scan, _ = self._resolved_real_fast_scan()
        confirmation = self.service.confirm_best(scan.scan_id, None)
        self.assertTrue(confirmation["current"])

        with self.database.connect() as conn:
            conn.execute(
                """UPDATE provider_episode_series_cache
                   SET source_signature='provider-v2'
                   WHERE provider='tvdb' AND provider_series_id='4242'
                     AND language='eng'"""
            )

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        stale_confirmation = self.service.confirmation_status(1)
        self.assertIsNotNone(stale_confirmation)
        self.assertFalse(stale_confirmation["current"])
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            self.service.confirm_best(scan.scan_id, None)
        self.assertEqual(self.service.rename_preview(scan.scan_id)["status"], "stale")

    def test_title_provider_identity_change_invalidates_scan(self) -> None:
        scan, _ = self._resolved_real_fast_scan()
        with self.database.connect() as conn:
            conn.execute("UPDATE titles SET tvdb_id=9999 WHERE id=1")

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_runtime_metadata_change_invalidates_scan(self) -> None:
        scan, _ = self._resolved_real_fast_scan()
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE files SET runtime_seconds=1500 WHERE id=1"
            )

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_stream_selection_change_invalidates_scan(self) -> None:
        scan, _ = self._resolved_real_fast_scan()
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO media_streams(
                     file_id,stream_index,stream_type,codec,language,title,
                     channels,default_flag,forced_flag,disposition_json
                   ) VALUES (1,7,'audio','AAC','eng','Added Track',2,0,0,'{}')"""
            )

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])

    def test_unselected_new_sidecar_invalidates_full_subtitle_selection(self) -> None:
        scan, sidecar = self._resolved_real_fast_scan()
        extra = self.media.with_suffix(".commentary.srt")
        extra.write_text(
            "1\n00:00:00,000 --> 00:00:03,000\n"
            "unrelated commentary subtitle that was not in the scan\n",
            encoding="utf-8",
        )
        self.assertTrue(sidecar.exists())

        detail = self.service.scan_detail(scan.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        self.assertEqual(self.service.rename_preview(scan.scan_id)["status"], "stale")

    def test_legacy_scan_without_complete_input_manifest_is_non_actionable(self) -> None:
        self.service.resolve_scan(self.scan_id)
        with self.database.connect() as conn:
            scan = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
            claimed = json.loads(scan["claimed_identity_json"])
            claimed.pop("input_signature_version", None)
            claimed.pop("input_signatures", None)
            conn.execute(
                """UPDATE media_identity_scans
                   SET claimed_identity_json=?,metadata_signature='legacy'
                   WHERE id=?""",
                (json.dumps(claimed), self.scan_id),
            )

        detail = self.service.scan_detail(self.scan_id)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        self.assertEqual(self.service.mie_findings(), [])
        self.assertEqual(self.service.rename_preview(self.scan_id)["status"], "stale")

    def test_persisted_candidate_identity_tamper_stales_resolved_result(self) -> None:
        self.service.resolve_scan(self.scan_id)
        with self.database.connect() as conn:
            best = conn.execute(
                "SELECT best_candidate_key FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
            conn.execute(
                """UPDATE media_identity_candidates
                   SET provider_item_id='tampered-provider-id'
                   WHERE scan_id=? AND candidate_key=?""",
                (self.scan_id, best["best_candidate_key"]),
            )

        detail = self.service.scan_detail(self.scan_id)
        self.assertEqual(detail["result_state"], "strong_match_other")
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        self.assertEqual(self.service.rename_preview(self.scan_id)["status"], "stale")
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            self.service.confirm_best(self.scan_id, None)

    def test_persisted_evidence_tamper_cannot_leave_stored_mismatch_actionable(self) -> None:
        self.service.resolve_scan(self.scan_id)
        with self.database.connect() as conn:
            conn.execute(
                """UPDATE media_identity_evidence
                   SET relation='neutral',strength=0
                   WHERE scan_id=? AND evidence_category='subtitle_text'""",
                (self.scan_id,),
            )

        detail = self.service.scan_detail(self.scan_id)
        self.assertEqual(detail["result_state"], "strong_match_other")
        self.assertEqual(detail["resolution_explanation"], self.service._resolve_snapshot(
            {
                **detail,
                "claimed_identity_json": json.dumps(detail["claimed_identity"]),
            },
            detail["candidates"],
            detail["evidence"],
        ).explanation)
        self.assertFalse(detail["snapshot_current"])
        self.assertFalse(detail["actionable"])
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            self.service.confirm_best(self.scan_id, None)

    def test_sealed_result_revision_is_immutable_and_resolution_advances_it(self) -> None:
        with self.database.connect() as conn:
            before = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
            self.assertEqual(
                result_revision(
                    {"claimed_identity_json": before["claimed_identity_json"]}
                ),
                1,
            )
            with self.assertRaises(DecisionSnapshotError):
                seal_decision_snapshot(conn, self.scan_id, revision=1)

        self.service.resolve_scan(self.scan_id)
        with self.database.connect() as conn:
            after = conn.execute(
                "SELECT claimed_identity_json FROM media_identity_scans WHERE id=?",
                (self.scan_id,),
            ).fetchone()
        claimed = json.loads(after["claimed_identity_json"])
        self.assertEqual(claimed["result_revision"], 2)
        self.assertEqual(claimed["decision_snapshot"]["revision"], 2)

    def test_file_change_blocks_confirmation(self) -> None:
        self.service.resolve_scan(self.scan_id)
        self.media.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed after this identity scan"):
            self.service.confirm_current(self.scan_id, None)


if __name__ == "__main__":
    unittest.main()
