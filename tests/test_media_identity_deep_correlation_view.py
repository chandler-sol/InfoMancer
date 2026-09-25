from __future__ import annotations

import unittest

from app.media_identity.deep_correlation_view import (
    DeepCorrelationViewError,
    _attach_display_context,
    _related_file_ids,
    _target_patterns,
)
from app.media_identity.service import MediaIdentityDecisionService


def _output(
    *,
    duplicates=None,
    swaps=None,
    cycles=None,
    sequence=None,
    ambiguous=None,
):
    return {
        "duplicates": list(duplicates or []),
        "swaps": list(swaps or []),
        "identity_cycles": list(cycles or []),
        "sequence": {
            "authoritative_observations": list(
                sequence or []
            ),
        },
        "ambiguous_claim_file_ids": list(
            ambiguous or []
        ),
    }


def _sequence(
    *,
    supporters,
    usable=(1, 2, 3, 4),
    offset=1,
):
    return {
        "season": 1,
        "offset": offset,
        "supporting_file_ids": list(supporters),
        "usable_file_ids": list(usable),
        "support_count": len(supporters),
        "usable_count": len(usable),
        "support_ratio": (
            len(supporters) / float(len(usable))
        ),
        "longest_chain": len(supporters),
        "first_claimed_episode": 1,
        "last_claimed_episode": 4,
    }


class DeepCorrelationViewStateTests(unittest.TestCase):
    def test_season_offset_does_not_flag_target_that_did_not_support_it(self) -> None:
        result = _target_patterns(
            _output(
                sequence=[
                    _sequence(supporters=(2, 3, 4))
                ],
            ),
            target_file_id=1,
            target_season=1,
        )

        self.assertEqual(
            result["review_state"],
            "no_cross_file_pattern",
        )
        self.assertFalse(
            result["sequence_observations"][0][
                "target_supports"
            ]
        )
        self.assertEqual(
            result["target_sequence_observations"],
            (),
        )

    def test_target_supported_sequence_offset_gets_read_only_summary(self) -> None:
        result = _target_patterns(
            _output(
                sequence=[
                    _sequence(supporters=(1, 2, 3))
                ],
            ),
            target_file_id=1,
            target_season=1,
        )

        self.assertEqual(
            result["review_state"],
            "sequence_offset",
        )
        self.assertIn("+1", result["review_explanation"])
        self.assertFalse(result["review_actionable"])

    def test_duplicate_has_priority_over_conflicted_swap(self) -> None:
        result = _target_patterns(
            _output(
                duplicates=[{
                    "left_file_id": 1,
                    "right_file_id": 2,
                    "strength": "strong_multimodal",
                    "agreement": "both_high",
                }],
                swaps=[{
                    "left_file_id": 1,
                    "right_file_id": 2,
                    "left_claimed": [1, 1],
                    "right_claimed": [1, 2],
                    "left_hypothesis": [1, 2],
                    "right_hypothesis": [1, 1],
                    "status": "conflicted_similarity",
                    "fingerprint_agreement": "both_high",
                }],
            ),
            target_file_id=1,
            target_season=1,
        )

        self.assertEqual(
            result["review_state"],
            "duplicate_content_identity",
        )
        self.assertFalse(result["review_actionable"])

    def test_corroborated_reciprocal_hypothesis_is_possible_swap(self) -> None:
        result = _target_patterns(
            _output(
                swaps=[{
                    "left_file_id": 1,
                    "right_file_id": 2,
                    "left_claimed": [1, 1],
                    "right_claimed": [1, 2],
                    "left_hypothesis": [1, 2],
                    "right_hypothesis": [1, 1],
                    "status": "corroborated_distinct",
                    "fingerprint_agreement": "both_low",
                }],
            ),
            target_file_id=1,
            target_season=1,
        )

        self.assertEqual(
            result["review_state"],
            "possible_swapped_episodes",
        )
        self.assertFalse(result["review_actionable"])

    def test_ambiguous_claim_is_visible_but_not_actionable(self) -> None:
        result = _target_patterns(
            _output(ambiguous=(1, 2)),
            target_file_id=1,
            target_season=1,
        )

        self.assertEqual(
            result["review_state"],
            "ambiguous_claim",
        )
        self.assertFalse(result["review_actionable"])

    def test_sequence_count_tamper_fails_closed(self) -> None:
        raw = _sequence(supporters=(1, 2, 3))
        raw["support_count"] = 4

        with self.assertRaisesRegex(
            DeepCorrelationViewError,
            "counts are inconsistent",
        ):
            _target_patterns(
                _output(sequence=[raw]),
                target_file_id=1,
                target_season=1,
            )


class DeepCorrelationDisplayContextTests(unittest.TestCase):
    def test_display_context_names_peers_without_changing_pattern_identity(self) -> None:
        patterns = _target_patterns(
            _output(
                duplicates=[{
                    "left_file_id": 1,
                    "right_file_id": 2,
                    "strength": "strong_multimodal",
                    "agreement": "both_high",
                }],
                sequence=[
                    _sequence(supporters=(1, 2, 3))
                ],
            ),
            target_file_id=1,
            target_season=1,
        )
        coverage = {
            "missing_scan_file_ids": (3,),
            "invalid_scan_file_ids": (),
        }
        related = _related_file_ids(
            patterns,
            coverage,
            target_file_id=1,
        )
        self.assertEqual(related, (2, 3))

        enriched, enriched_coverage = _attach_display_context(
            patterns,
            coverage,
            {
                2: {
                    "id": 2,
                    "filename": "Example - S01E02.mkv",
                    "season": 1,
                    "episode_start": 2,
                    "episode_end": 2,
                },
                3: {
                    "id": 3,
                    "filename": "Example - S01E03.mkv",
                    "season": 1,
                    "episode_start": 3,
                    "episode_end": 3,
                },
            },
        )

        self.assertEqual(
            enriched["duplicates"][0]["other_file"]["filename"],
            "Example - S01E02.mkv",
        )
        self.assertEqual(
            [
                item["filename"]
                for item in enriched["sequence_observations"][0][
                    "supporting_files"
                ]
            ],
            [
                "File #1",
                "Example - S01E02.mkv",
                "Example - S01E03.mkv",
            ],
        )
        self.assertEqual(
            enriched_coverage["missing_scan_files"][0]["filename"],
            "Example - S01E03.mkv",
        )


class DeepCorrelationMieAdvisoryTests(unittest.TestCase):
    @staticmethod
    def _detail(analysis):
        return {
            "id": 7,
            "file_id": 1,
            "result_state": "probably_correct",
            "result_revision": 12,
            "decision_snapshot_sha256": "a" * 64,
            "completed_profile": "deep",
            "file": {
                "filename": "Example - S01E01.mkv",
                "root_id": 3,
                "title_id": 4,
            },
            "deep_correlation_analysis": {
                "artifact_id": 900,
                "coverage": {
                    "fully_multimodal": True,
                    "complete_modalities": ("video", "audio"),
                    "sequence_peer_coverage_complete": True,
                },
                **analysis,
            },
        }

    def test_sequence_pattern_becomes_read_only_mie_advisory(self) -> None:
        analysis = _target_patterns(
            _output(
                sequence=[
                    _sequence(supporters=(1, 2, 3))
                ],
            ),
            target_file_id=1,
            target_season=1,
        )

        finding = MediaIdentityDecisionService._deep_correlation_finding(
            self._detail(analysis)
        )

        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(
            finding["rule_key"],
            "episode-identity-deep-review",
        )
        self.assertEqual(finding["severity"], "information")
        self.assertEqual(
            finding["evidence"]["deep_review_state"],
            "sequence_offset",
        )
        self.assertFalse(
            finding["evidence"]["deep_review_actionable"]
        )
        self.assertEqual(
            finding["evidence"]["related_file_ids"],
            [2, 3],
        )
        self.assertEqual(
            finding["evidence"]["sequence_offsets"],
            [1],
        )

    def test_duplicate_advisory_is_warning_but_stays_non_actionable(self) -> None:
        analysis = _target_patterns(
            _output(
                duplicates=[{
                    "left_file_id": 1,
                    "right_file_id": 2,
                    "strength": "strong_multimodal",
                    "agreement": "both_high",
                }],
            ),
            target_file_id=1,
            target_season=1,
        )

        finding = MediaIdentityDecisionService._deep_correlation_finding(
            self._detail(analysis)
        )

        self.assertIsNotNone(finding)
        assert finding is not None
        self.assertEqual(finding["severity"], "warning")
        self.assertFalse(
            finding["evidence"]["deep_review_actionable"]
        )
        self.assertIn(
            "cannot rename or confirm media by itself",
            finding["recommendation"],
        )

    def test_no_target_pattern_does_not_create_mie_noise(self) -> None:
        analysis = _target_patterns(
            _output(
                sequence=[
                    _sequence(supporters=(2, 3, 4))
                ],
            ),
            target_file_id=1,
            target_season=1,
        )

        self.assertIsNone(
            MediaIdentityDecisionService._deep_correlation_finding(
                self._detail(analysis)
            )
        )


if __name__ == "__main__":
    unittest.main()
