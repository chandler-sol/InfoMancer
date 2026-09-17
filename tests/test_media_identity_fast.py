from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.candidates import generate_episode_candidates
from app.media_identity.fast import FastIdentityService, FastIdentityStaleError
from app.media_identity.text import normalize_subtitle_text


REGULAR_OVERVIEWS = {
    1: "amber falcon orchard glacier velvet compass",
    2: "bronze harbor lantern meadow quartz thunder",
    3: "cobalt garden rocket willow marble canyon",
    4: "dragon museum pepper sunrise timber violin",
    5: "emerald bakery comet river silver trumpet",
    6: "forest castle mirror ocean copper phoenix",
    7: "meteor archive lantern harbor silver compass",
    8: "meteor archive lantern harbor crimson compass",
    9: "island jacket kingdom lemon mountain nectar",
    10: "opal palace rabbit shadow tunnel unicorn",
    11: "prairie engine sapphire temple winter zebra",
    12: "acorn bridge crystal desert feather galaxy",
}
SPECIAL_OVERVIEWS = {
    1: "holiday sleigh reindeer chimney cocoa snowflake",
    2: "backstage reunion costume blooper camera applause",
}


class FastEpisodeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.media_root = self.root / "media"
        self.show_root = self.media_root / "Example Show"
        self.show_root.mkdir(parents=True)
        self.database = Database(self.root / "catalog.db")
        self.database.initialize()
        self.next_file_id = 1
        self._seed_catalog()
        self.service = FastIdentityService(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_catalog(self) -> None:
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
                """INSERT INTO provider_episode_series_cache(
                     provider,provider_series_id,language,source_signature,
                     episode_count,mapping_count,order_namespaces_json
                   ) VALUES ('tvdb','4242','eng','provider-v1',14,15,
                             '[{"namespace":"default"},{"namespace":"production"}]')"""
            )

            identities = []
            mappings = []
            expected = []
            for episode, overview in REGULAR_OVERVIEWS.items():
                provider_id = str(1000 + episode)
                identities.append((
                    "tvdb", "4242", provider_id, "eng",
                    f"Episode {episode} Distinctive", overview, f"2026-01-{episode:02d}",
                    json.dumps({"runtime": 24}),
                ))
                mappings.append((
                    "tvdb", "4242", provider_id, "eng", "default", "Default",
                    1, episode, episode, json.dumps([1, episode, episode]), "{}",
                ))
                expected.append((episode, 1, 1000 + episode, 1, episode, f"Episode {episode} Distinctive"))

            for episode, overview in SPECIAL_OVERVIEWS.items():
                provider_id = str(2000 + episode)
                identities.append((
                    "tvdb", "4242", provider_id, "eng",
                    f"Special {episode} Distinctive", overview, f"2026-02-{episode:02d}",
                    json.dumps({"runtime": 24}),
                ))
                mappings.append((
                    "tvdb", "4242", provider_id, "eng", "default", "Default",
                    0, episode, None, json.dumps([0, episode, None]), "{}",
                ))
                expected.append((100 + episode, 1, 2000 + episode, 0, episode, f"Special {episode} Distinctive"))

            # One content identity intentionally has a second numbering map. It must
            # remain one candidate because provider episode 1003 is one episode.
            mappings.append((
                "tvdb", "4242", "1003", "eng", "production", "Production",
                1, 30, 30, json.dumps([1, 30, 30]), "{}",
            ))

            conn.executemany(
                """INSERT INTO provider_episode_identities(
                     provider,provider_series_id,provider_episode_id,language,
                     name,overview,aired,metadata_json
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                identities,
            )
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
                expected,
            )

    @staticmethod
    def _subtitle_payload(text: str) -> str:
        return (
            "1\n00:00:00,000 --> 00:00:05,000\n"
            + text
            + "\n\n2\n00:00:06,000 --> 00:00:09,000\n"
            + text
            + "\n"
        )

    def _add_file(
        self,
        claim_start: int,
        *,
        claim_end: int | None = None,
        actual_episode: int | None = None,
        actual_special: int | None = None,
        sidecar_text: str | None = None,
        add_streams: bool = True,
        add_hash: bool = False,
    ) -> int:
        file_id = self.next_file_id
        self.next_file_id += 1
        claim_end = claim_start if claim_end is None else claim_end
        episode_token = f"S01E{claim_start:02d}"
        if claim_end != claim_start:
            episode_token += f"-E{claim_end:02d}"
        media_path = self.show_root / f"{episode_token}.fixture{file_id}.mkv"
        payload = (f"fixture-media-{file_id}-" * 20).encode("utf-8")
        media_path.write_bytes(payload)
        stat = media_path.stat()

        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,runtime_seconds,
                     width,height,video_codec,audio_codec,audio_channels,bitrate,
                     container,dynamic_range,media_info_at,media_info_error,seen_scan
                   ) VALUES (?,1,?,?,?,?,?,1,?,?,?,1440,1920,1080,
                             'H264','AAC',2,5000000,'MKV','SDR','2026-09-17T12:00:00','',
                             'fixture-scan')""",
                (
                    file_id,
                    str(media_path),
                    media_path.name,
                    "mkv",
                    len(payload),
                    stat.st_mtime,
                    claim_start,
                    claim_end,
                    "Example Show",
                ),
            )
            if add_streams:
                conn.executemany(
                    """INSERT INTO media_streams(
                         file_id,stream_index,stream_type,codec,language,title,
                         channels,default_flag,forced_flag,disposition_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (file_id, 0, "video", "H264", "und", "", None, 1, 0, "{}"),
                        (file_id, 1, "audio", "AAC", "eng", "Main", 2, 1, 0, "{}"),
                        (file_id, 2, "subtitle", "SUBRIP", "eng", "English", None, 1, 0, "{}"),
                    ],
                )
            if add_hash:
                conn.execute(
                    """INSERT INTO media_file_hashes(
                         file_id,sha256,size_bytes,modified_at,status,hashed_at
                       ) VALUES (?,?,?,?, 'complete',CURRENT_TIMESTAMP)""",
                    (
                        file_id,
                        hashlib.sha256(payload).hexdigest(),
                        len(payload),
                        stat.st_mtime,
                    ),
                )

        if sidecar_text is None:
            if actual_episode is not None:
                sidecar_text = REGULAR_OVERVIEWS[actual_episode]
            elif actual_special is not None:
                sidecar_text = SPECIAL_OVERVIEWS[actual_special]
        if sidecar_text is not None:
            media_path.with_suffix(".en.srt").write_text(
                self._subtitle_payload(sidecar_text), encoding="utf-8"
            )
        return file_id

    def _candidate_provider_ids(self, scan_id: int) -> list[str]:
        with self.database.connect() as conn:
            return [
                str(row["provider_item_id"])
                for row in conn.execute(
                    """SELECT provider_item_id FROM media_identity_candidates
                       WHERE scan_id=? ORDER BY rank""",
                    (scan_id,),
                )
            ]

    def _subtitle_scores(self, scan_id: int) -> dict[str, float]:
        with self.database.connect() as conn:
            rows = conn.execute(
                """SELECT c.provider_item_id,e.details_json
                   FROM media_identity_evidence e
                   JOIN media_identity_candidates c
                     ON c.scan_id=e.scan_id AND c.candidate_key=e.candidate_key
                   WHERE e.scan_id=? AND e.analyzer_key='subtitle-synopsis'
                     AND e.candidate_key!=''
                   ORDER BY e.id""",
                (scan_id,),
            ).fetchall()
        result: dict[str, float] = {}
        for row in rows:
            details = json.loads(row["details_json"] or "{}")
            result[str(row["provider_item_id"])] = float(details.get("similarity") or 0.0)
        return result

    def test_subtitle_normalizer_handles_srt_vtt_and_ass_transport_markup(self) -> None:
        srt = "1\n00:00:01,000 --> 00:00:02,000\n<b>Amber Falcon</b>\n"
        vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nAmber &amp; Falcon\n"
        ass = (
            "[Script Info]\n[Events]\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}Amber\\Nfalcon\n"
        )
        self.assertEqual(normalize_subtitle_text(srt, ".srt"), "amber falcon")
        self.assertEqual(normalize_subtitle_text(vtt, ".vtt"), "amber & falcon")
        self.assertEqual(normalize_subtitle_text(ass, ".ass"), "amber falcon")

    def test_alternate_order_mappings_remain_one_content_candidate(self) -> None:
        with self.database.connect() as conn:
            candidate_set = generate_episode_candidates(
                conn,
                title_id=1,
                season=1,
                episode_start=3,
                language="eng",
            )
        matches = [
            candidate for candidate in candidate_set.candidates
            if candidate.identity.provider_item_id == "1003"
        ]
        self.assertEqual(len(matches), 1)
        namespaces = {
            mapping["order_namespace"]
            for mapping in matches[0].details["mappings"]
        }
        self.assertEqual(namespaces, {"default", "production"})
        self.assertEqual(matches[0].details["origins"][0], "claimed_coordinate")

    def test_fast_scan_persists_explainable_evidence_without_final_verdict(self) -> None:
        file_id = self._add_file(1, actual_episode=1, add_hash=True)
        with self.database.connect() as conn:
            media_path = Path(conn.execute(
                "SELECT path FROM files WHERE id=?", (file_id,)
            ).fetchone()["path"])
        before = media_path.read_bytes()

        result = self.service.scan_file(file_id)
        details = self.service.scan_details(result.scan_id)
        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details["status"], "complete")
        self.assertEqual(details["requested_profile"], "fast")
        self.assertEqual(details["completed_profile"], "fast")
        self.assertEqual(details["stage"], "fast_complete")
        self.assertIsNone(details["result_state"])
        self.assertIsNone(details["best_candidate_key"])
        self.assertTrue(details["file_sha256"])
        self.assertGreaterEqual(len(details["candidates"]), 12)

        container = next(
            item for item in details["evidence"]
            if item["analyzer_key"] == "catalog-media-info"
        )
        self.assertEqual(len(container["details"]["streams"]), 3)
        self.assertFalse(container["details"]["embedded_subtitle_text_extracted"])
        self.assertEqual(media_path.read_bytes(), before)

    def test_sidecar_artifact_is_reused_on_repeated_fast_scan(self) -> None:
        file_id = self._add_file(2, actual_episode=2)
        first = self.service.scan_file(file_id)
        self.assertEqual(first.artifact_count, 1)
        self.assertEqual(first.reused_artifact_count, 0)
        with self.database.connect() as conn:
            first_artifacts = conn.execute(
                "SELECT COUNT(*) FROM media_identity_artifacts WHERE file_id=?",
                (file_id,),
            ).fetchone()[0]

        second = self.service.scan_file(file_id)
        self.assertEqual(second.artifact_count, 1)
        self.assertEqual(second.reused_artifact_count, 1)
        with self.database.connect() as conn:
            second_artifacts = conn.execute(
                "SELECT COUNT(*) FROM media_identity_artifacts WHERE file_id=?",
                (file_id,),
            ).fetchone()[0]
        self.assertEqual(first_artifacts, 1)
        self.assertEqual(second_artifacts, 1)

    def test_missing_subtitles_are_neutral_not_mismatch_evidence(self) -> None:
        file_id = self._add_file(3)
        result = self.service.scan_file(file_id)
        details = self.service.scan_details(result.scan_id)
        assert details is not None
        subtitle = [
            item for item in details["evidence"]
            if item["evidence_category"] == "subtitle_text"
        ]
        self.assertEqual(len(subtitle), 1)
        self.assertEqual(subtitle[0]["relation"], "neutral")
        self.assertEqual(subtitle[0]["strength"], 0.0)
        self.assertIsNone(details["result_state"])

    def test_changed_media_is_rejected_before_any_scan_is_persisted(self) -> None:
        file_id = self._add_file(4, actual_episode=4)
        with self.database.connect() as conn:
            path = Path(conn.execute(
                "SELECT path FROM files WHERE id=?", (file_id,)
            ).fetchone()["path"])
        path.write_bytes(path.read_bytes() + b"changed")

        with self.assertRaises(FastIdentityStaleError):
            self.service.scan_file(file_id)
        with self.database.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM media_identity_scans WHERE file_id=?",
                    (file_id,),
                ).fetchone()[0],
                0,
            )

    def test_persistence_failure_rolls_back_scan_candidates_evidence_and_artifact(self) -> None:
        file_id = self._add_file(5, actual_episode=5)
        with self.database.connect() as conn:
            conn.execute(
                """CREATE TRIGGER fail_fast_evidence
                   BEFORE INSERT ON media_identity_evidence
                   BEGIN SELECT RAISE(FAIL,'fixture rollback'); END"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.scan_file(file_id)
        with self.database.connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM media_identity_scans WHERE file_id=?",
                    (file_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM media_identity_artifacts WHERE file_id=?",
                    (file_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM media_identity_candidates").fetchone()[0],
                0,
            )

    def test_multi_episode_claim_marks_each_claimed_content_candidate(self) -> None:
        file_id = self._add_file(4, claim_end=5, actual_episode=4)
        result = self.service.scan_file(file_id)
        with self.database.connect() as conn:
            claimed = {
                str(row["provider_item_id"])
                for row in conn.execute(
                    """SELECT c.provider_item_id
                       FROM media_identity_evidence e
                       JOIN media_identity_candidates c
                         ON c.scan_id=e.scan_id AND c.candidate_key=e.candidate_key
                       WHERE e.scan_id=? AND e.analyzer_key='catalog-claim'
                         AND e.evidence_category='claimed_identity'
                         AND e.relation='supports'""",
                    (result.scan_id,),
                )
            }
        self.assertEqual(claimed, {"1004", "1005"})

    def test_weak_regular_text_expands_to_bounded_special_candidates(self) -> None:
        file_id = self._add_file(6, actual_special=1)
        result = self.service.scan_file(file_id)
        self.assertTrue(result.expanded_specials)
        self.assertLessEqual(result.candidate_count, 80)
        provider_ids = self._candidate_provider_ids(result.scan_id)
        self.assertIn("2001", provider_ids)
        scores = self._subtitle_scores(result.scan_id)
        self.assertGreaterEqual(scores["2001"], 0.30)
        details = self.service.scan_details(result.scan_id)
        assert details is not None
        self.assertIsNone(details["result_state"])

    def test_adjacent_similar_synopses_remain_evidence_not_a_final_decision(self) -> None:
        file_id = self._add_file(7, actual_episode=7)
        result = self.service.scan_file(file_id)
        scores = self._subtitle_scores(result.scan_id)
        self.assertGreater(scores["1007"], scores["1008"])
        self.assertGreaterEqual(scores["1008"], 0.30)
        details = self.service.scan_details(result.scan_id)
        assert details is not None
        self.assertIsNone(details["result_state"])

    def test_misleading_dialogue_can_support_another_candidate_without_declaring_mismatch(self) -> None:
        file_id = self._add_file(9, actual_episode=10)
        result = self.service.scan_file(file_id)
        scores = self._subtitle_scores(result.scan_id)
        self.assertGreater(scores["1010"], scores["1009"])
        details = self.service.scan_details(result.scan_id)
        assert details is not None
        self.assertIsNone(details["result_state"])
        self.assertIsNone(details["best_candidate_key"])

    def test_hostile_cohort_has_three_times_more_correct_than_mislabeled_files(self) -> None:
        # The fixture deliberately prevents a wrong-heavy test distribution from making
        # the analyzer look safer than it is. Nine files agree with their names, while
        # three contain another episode's dialogue.
        actual_by_claim = {
            1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9,
            10: 11, 11: 12, 12: 10,
        }
        observed_correct = 0
        observed_wrong = 0
        for claim, actual in actual_by_claim.items():
            file_id = self._add_file(claim, actual_episode=actual, add_streams=False)
            result = self.service.scan_file(file_id)
            scores = self._subtitle_scores(result.scan_id)
            strongest = max(scores, key=scores.get)
            self.assertEqual(strongest, str(1000 + actual))
            details = self.service.scan_details(result.scan_id)
            assert details is not None
            self.assertIsNone(details["result_state"])
            if claim == actual:
                observed_correct += 1
            else:
                observed_wrong += 1
        self.assertEqual((observed_correct, observed_wrong), (9, 3))


if __name__ == "__main__":
    unittest.main()