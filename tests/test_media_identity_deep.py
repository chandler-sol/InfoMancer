from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.media_identity.deep import (
    MAX_DEEP_CANDIDATES,
    MAX_DEEP_CORRELATION_FILES,
    MAX_DEEP_PAIRWISE_COMPARISONS,
    MAX_DEEP_SPECIALS,
    DeepCandidatePolicy,
    DeepCorrelationPolicy,
    DeepIdentityError,
    generate_deep_episode_candidates,
    plan_deep_correlation,
)


class DeepIdentityPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "deep.db")
        self.database.initialize()

        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,'/tv','tv','TV')"
            )
            conn.executemany(
                """INSERT INTO titles(
                     id,root_id,kind,title,folder_path
                   ) VALUES (?,1,'tv',?,?)""",
                [
                    (1, "Example Show", "/tv/Example Show"),
                    (2, "Other Show", "/tv/Other Show"),
                ],
            )
            conn.executemany(
                """INSERT INTO expected_episodes(
                     id,title_id,tvdb_episode_id,season,episode,name
                   ) VALUES (?,?,?,?,?,?)""",
                [
                    (1, 1, 1001, 2, 1, "S2 One"),
                    (2, 1, 1002, 2, 2, "S2 Two"),
                    (3, 1, 9001, 0, 1, "Special"),
                    (4, 1, 1101, 1, 1, "S1 One"),
                    (5, 1, 1102, 1, 2, "S1 Two"),
                    (6, 1, 1301, 3, 1, "S3 One"),
                    (7, 2, 2001, 2, 1, "Other"),
                ],
            )
            conn.executemany(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,seen_scan
                   ) VALUES (?,?,?,?, 'mkv', ?, ?, ?, ?, ?, ?, 'scan')""",
                [
                    (
                        1, 1, "/tv/Example Show/S02E01.mkv", "S02E01.mkv",
                        101, 1.0, 2, 1, 1, "Example Show",
                    ),
                    (
                        2, 1, "/tv/Example Show/S02E02.mkv", "S02E02.mkv",
                        102, 2.0, 2, 2, 2, "Example Show",
                    ),
                    (
                        3, 1, "/tv/Example Show/S01E01.mkv", "S01E01.mkv",
                        103, 3.0, 1, 1, 1, "Example Show",
                    ),
                    (
                        4, 1, "/tv/Example Show/S03E01.mkv", "S03E01.mkv",
                        104, 4.0, 3, 1, 1, "Example Show",
                    ),
                    (
                        5, 1, "/tv/Example Show/S00E01.mkv", "S00E01.mkv",
                        105, 5.0, 0, 1, 1, "Example Show",
                    ),
                    (
                        6, 2, "/tv/Other Show/S02E01.mkv", "S02E01.mkv",
                        106, 6.0, 2, 1, 1, "Other Show",
                    ),
                ],
            )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_deep_candidate_plan_widens_in_safe_priority_order(self) -> None:
        with self.database.connect() as conn:
            plan = generate_deep_episode_candidates(
                conn,
                title_id=1,
                season=2,
                episode_start=1,
                policy=DeepCandidatePolicy(
                    adjacent_season_radius=1,
                    include_specials=True,
                    max_candidates=10,
                    max_specials=1,
                ),
            )

        coordinates = [
            (item.identity.season, item.identity.episode)
            for item in plan.candidates
        ]
        self.assertEqual(
            coordinates,
            [
                (2, 1),
                (2, 2),
                (0, 1),
                (1, 1),
                (1, 2),
                (3, 1),
            ],
        )
        self.assertIn(
            "deep_special",
            plan.candidates[2].details["origins"],
        )
        self.assertIn(
            "deep_adjacent_season",
            plan.candidates[3].details["origins"],
        )
        self.assertEqual(
            [item.rank for item in plan.candidates],
            list(range(1, len(plan.candidates) + 1)),
        )
        self.assertEqual(len(plan.plan_signature), 64)

    def test_deep_candidate_plan_is_deterministic_and_bound(self) -> None:
        policy = DeepCandidatePolicy(
            adjacent_season_radius=1,
            include_specials=True,
            max_candidates=4,
            max_specials=1,
        )
        with self.database.connect() as conn:
            first = generate_deep_episode_candidates(
                conn,
                title_id=1,
                season=2,
                episode_start=1,
                policy=policy,
            )
            second = generate_deep_episode_candidates(
                conn,
                title_id=1,
                season=2,
                episode_start=1,
                policy=policy,
            )

        self.assertEqual(first.plan_signature, second.plan_signature)
        self.assertEqual(len(first.candidates), 4)
        self.assertEqual(
            [(item.identity.season, item.identity.episode) for item in first.candidates],
            [(2, 1), (2, 2), (0, 1), (1, 1)],
        )

    def test_deep_special_claim_does_not_treat_regular_seasons_as_adjacent(self) -> None:
        with self.database.connect() as conn:
            plan = generate_deep_episode_candidates(
                conn,
                title_id=1,
                season=0,
                episode_start=1,
                policy=DeepCandidatePolicy(
                    adjacent_season_radius=2,
                    include_specials=True,
                    max_candidates=10,
                ),
            )
        self.assertEqual(
            [(item.identity.season, item.identity.episode) for item in plan.candidates],
            [(0, 1)],
        )

    def test_candidate_policy_rejects_unbounded_work(self) -> None:
        bad = (
            {"adjacent_season_radius": 3},
            {"max_candidates": MAX_DEEP_CANDIDATES + 1},
            {"max_specials": MAX_DEEP_SPECIALS + 1},
            {"adjacent_season_radius": True},
        )
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DeepIdentityError):
                    DeepCandidatePolicy(**kwargs)

    def test_correlation_defaults_to_same_title_same_season(self) -> None:
        with self.database.connect() as conn:
            plan = plan_deep_correlation(conn, file_id=1)

        self.assertEqual(plan.target.file_id, 1)
        self.assertEqual([item.file_id for item in plan.peers], [2])
        self.assertEqual(plan.comparison_pairs, ((1, 2),))
        self.assertEqual(len(plan.plan_signature), 64)

    def test_correlation_can_widen_but_excludes_specials_and_other_titles(self) -> None:
        policy = DeepCorrelationPolicy(
            season_radius=1,
            max_files=4,
            max_pairwise_comparisons=6,
        )
        with self.database.connect() as conn:
            plan = plan_deep_correlation(
                conn,
                file_id=1,
                policy=policy,
            )

        self.assertEqual([item.file_id for item in plan.peers], [2, 3, 4])
        self.assertNotIn(5, [item.file_id for item in plan.files])
        self.assertNotIn(6, [item.file_id for item in plan.files])
        self.assertEqual(
            plan.comparison_pairs,
            (
                (1, 2),
                (1, 3),
                (1, 4),
                (2, 3),
                (2, 4),
                (3, 4),
            ),
        )

    def test_pair_budget_prioritizes_target_to_peer_comparisons(self) -> None:
        policy = DeepCorrelationPolicy(
            season_radius=1,
            max_files=3,
            max_pairwise_comparisons=2,
        )
        with self.database.connect() as conn:
            plan = plan_deep_correlation(
                conn,
                file_id=1,
                policy=policy,
            )

        self.assertEqual([item.file_id for item in plan.peers], [2, 3])
        self.assertEqual(plan.comparison_pairs, ((1, 2), (1, 3)))

    def test_correlation_signature_binds_file_snapshot(self) -> None:
        policy = DeepCorrelationPolicy(
            season_radius=0,
            max_files=2,
            max_pairwise_comparisons=1,
        )
        with self.database.connect() as conn:
            before = plan_deep_correlation(conn, file_id=1, policy=policy)
            conn.execute(
                "UPDATE files SET modified_at=modified_at+1 WHERE id=2"
            )
            after = plan_deep_correlation(conn, file_id=1, policy=policy)

        self.assertNotEqual(before.plan_signature, after.plan_signature)

    def test_correlation_policy_rejects_unbounded_or_incomplete_work(self) -> None:
        bad = (
            {"season_radius": 3},
            {"max_files": MAX_DEEP_CORRELATION_FILES + 1},
            {"max_pairwise_comparisons": MAX_DEEP_PAIRWISE_COMPARISONS + 1},
            {"max_files": 4, "max_pairwise_comparisons": 2},
        )
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DeepIdentityError):
                    DeepCorrelationPolicy(**kwargs)


if __name__ == "__main__":
    unittest.main()
