import unittest

from app.routes.health_action_routing import health_finding_href


class LibraryHealthActionRoutingTests(unittest.TestCase):
    def test_missing_provider_identifier_opens_title_match_workflow(self):
        finding = {
            "rule_key": "metadata-identifiers-missing",
            "title_id": 42,
            "root_id": 7,
        }
        self.assertEqual(health_finding_href(finding), "/titles/42/tvdb")

    def test_title_metadata_findings_do_not_fall_through_to_sources(self):
        for rule_key in (
            "metadata-artwork-missing",
            "metadata-credits-missing",
            "metadata-episodes-incomplete",
            "metadata-stale",
        ):
            with self.subTest(rule_key=rule_key):
                finding = {"rule_key": rule_key, "title_id": 42, "root_id": 7}
                self.assertEqual(health_finding_href(finding), "/titles/42")

    def test_identity_findings_open_identity_review(self):
        for rule_key in ("identity-confidence-low", "unmatched-title"):
            with self.subTest(rule_key=rule_key):
                finding = {"rule_key": rule_key, "title_id": 42, "root_id": 7}
                self.assertEqual(health_finding_href(finding), "/titles/42/identity")

    def test_source_findings_remain_source_actions(self):
        for rule_key in ("source-stale", "source-offline", "source-degraded"):
            with self.subTest(rule_key=rule_key):
                finding = {"rule_key": rule_key, "root_id": 7}
                self.assertEqual(health_finding_href(finding), "/sources")

    def test_specialized_destinations_stay_intact(self):
        self.assertEqual(
            health_finding_href({"rule_key": "missing-episodes", "title_id": 42, "root_id": 7}),
            "/titles/42?show_missing=1#missing-panel",
        )
        self.assertEqual(
            health_finding_href({"rule_key": "technical-details-missing", "root_id": 7}),
            "/settings/system#media-information",
        )
        self.assertEqual(
            health_finding_href({"rule_key": "duplicate-storage-recovery", "title_id": 42}),
            "/duplicates",
        )


if __name__ == "__main__":
    unittest.main()
