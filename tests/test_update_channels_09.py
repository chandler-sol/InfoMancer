from pathlib import Path
import tempfile
import unittest

from app.update_channels import (
    channel_transition,
    read_update_channel,
    release_channel,
    select_release,
    update_state,
    version_key,
    write_update_channel,
)


ROOT = Path(__file__).resolve().parents[1]


class UpdateChannel09Tests(unittest.TestCase):
    def test_release_channel_classification(self):
        self.assertEqual(release_channel("v1.2.3"), "standard")
        self.assertEqual(release_channel("v1.2.3-beta.4", True), "beta")
        self.assertEqual(release_channel("v1.2.3-rc.1", True), "beta")
        self.assertEqual(release_channel("v1.2.3-dev.91", True), "dev")
        self.assertEqual(release_channel("v1.2.3-alpha.2", True), "dev")
        self.assertEqual(release_channel("v1.2.3-preview.8", True), "dev")
        self.assertEqual(release_channel("v1.2.3-experimental.1", True), "beta")

    def test_semver_stage_ordering(self):
        self.assertLess(version_key("0.9.0-dev.12"), version_key("0.9.0-beta.1"))
        self.assertLess(version_key("0.9.0-beta.9"), version_key("0.9.0"))
        self.assertLess(version_key("0.9.0-dev.9"), version_key("0.9.0-dev.10"))

    def test_channel_selection_never_leaks_less_stable_release(self):
        releases = [
            {"tag_name": "v0.8.2", "draft": False, "prerelease": False},
            {"tag_name": "v0.9.0-beta.2", "draft": False, "prerelease": True},
            {"tag_name": "v0.10.0-dev.18", "draft": False, "prerelease": True},
            {"tag_name": "v9.9.9-dev.999", "draft": True, "prerelease": True},
        ]
        self.assertEqual(select_release(releases, "standard")["tag_name"], "v0.8.2")
        self.assertEqual(select_release(releases, "beta")["tag_name"], "v0.9.0-beta.2")
        self.assertEqual(select_release(releases, "dev")["tag_name"], "v0.10.0-dev.18")

    def test_switching_to_more_stable_channel_never_requests_downgrade(self):
        self.assertEqual(channel_transition("dev", "standard"), "more_stable")
        self.assertEqual(update_state("0.10.0-dev.18", "0.9.0"), "waiting_for_channel")
        self.assertEqual(update_state("0.9.0-beta.1", "0.9.0-beta.2"), "available")

    def test_channel_preference_defaults_to_standard_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "infomancer.db"
            self.assertEqual(read_update_channel(database), "standard")
            write_update_channel(database, "dev")
            self.assertEqual(read_update_channel(database), "dev")
            (Path(directory) / "update-channel.json").write_text("not-json", encoding="utf-8")
            self.assertEqual(read_update_channel(database), "standard")

    def test_updates_page_and_routes_are_registered(self):
        routes = (ROOT / "app" / "routes" / "__init__.py").read_text(encoding="utf-8")
        handler = (ROOT / "app" / "routes" / "update_channel_settings.py").read_text(encoding="utf-8")
        nav = (ROOT / "app" / "templates" / "_settings_nav.html").read_text(encoding="utf-8")
        template = (ROOT / "app" / "templates" / "settings_updates.html").read_text(encoding="utf-8")
        self.assertIn("build_update_channel_settings_router", routes)
        self.assertIn('"/settings/updates"', handler)
        self.assertIn('"/settings/updates/channel"', handler)
        self.assertIn('"/settings/updates/check"', handler)
        self.assertIn('"/settings/updates/apply"', handler)
        self.assertIn('href="/settings/updates"', nav)
        self.assertIn("Standard", template)
        self.assertIn("Beta", template)
        self.assertIn("Dev", template)
        self.assertIn("will not downgrade", template)


if __name__ == "__main__":
    unittest.main()
