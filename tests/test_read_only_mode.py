import tempfile
import unittest
from pathlib import Path

from app.app_settings import AppSettings
from app.db import Database
from app.file_protection import FileProtectionService, MediaWriteBlocked


class ReadOnlyModeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "catalog.db")
        self.database.initialize()
        self.settings = AppSettings(self.database, "https://example.test/?q={query}")
        self.protection = FileProtectionService(self.settings)

    def tearDown(self):
        self.temporary.cleanup()

    def test_read_only_blocks_media_writes_but_standard_and_lockdown_allow_reviewed_changes(self):
        self.settings.update(self.settings.validate_safety("readonly"), None)
        self.assertEqual(self.protection.mode, "readonly")
        self.assertFalse(self.protection.media_writes_allowed)
        self.assertFalse(self.protection.automatic_permanent_delete_allowed)
        with self.assertRaisesRegex(MediaWriteBlocked, "Read-Only Mode"):
            self.protection.require_media_write("rename a file")
        self.settings.update(self.settings.validate_safety("standard"), None)
        self.protection.require_media_write("rename a file")
        self.assertTrue(self.protection.automatic_permanent_delete_allowed)
        self.settings.update(self.settings.validate_safety("lockdown"), None)
        self.protection.require_media_write("rename a file")
        self.assertFalse(self.protection.automatic_permanent_delete_allowed)

    def test_filesystem_mutating_routes_use_the_central_read_only_gate(self):
        root = Path(__file__).resolve().parents[1]
        titles = (root / "app/routes/titles.py").read_text(encoding="utf-8")
        review = (root / "app/routes/review.py").read_text(encoding="utf-8")
        operations = (root / "app/routes/operations.py").read_text(encoding="utf-8")
        background = (root / "app/background.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(titles.count("file_protection.require_media_write"), 5)
        self.assertGreaterEqual(review.count("file_protection.require_media_write"), 2)
        self.assertIn('file_protection.require_media_write("undo filesystem operations")', operations)
        self.assertIn('protection_mode in {"readonly", "lockdown"}', background)

    def test_read_only_banner_and_three_mode_settings_are_visible(self):
        root = Path(__file__).resolve().parents[1]
        base = (root / "app/templates/base.html").read_text(encoding="utf-8")
        settings = (root / "app/templates/settings.html").read_text(encoding="utf-8")
        self.assertIn("read-only-mode-banner", base)
        self.assertIn('value="readonly"', settings)
        self.assertIn('value="standard"', settings)
        self.assertIn('value="lockdown"', settings)


if __name__ == "__main__":
    unittest.main()
