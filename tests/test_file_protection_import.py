import tempfile
import unittest
from pathlib import Path

from app.app_settings import AppSettings
from app.db import Database


class FileProtectionImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "settings.db")
        self.database.initialize()
        self.settings = AppSettings(
            self.database, "https://example.test/search?q={query}"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_partial_read_only_import_explicitly_disables_lockdown(self):
        self.settings.update(self.settings.validate_safety("lockdown"), None)
        imported = self.settings.validate_import({"read_only_mode": "1"})
        self.assertEqual(
            imported,
            {"read_only_mode": "1", "lockdown_mode": "0"},
        )
        self.settings.update(imported, None)
        self.assertEqual(self.settings.file_protection_mode(), "readonly")

    def test_partial_lockdown_import_explicitly_disables_read_only(self):
        self.settings.update(self.settings.validate_safety("readonly"), None)
        imported = self.settings.validate_import({"lockdown_mode": "1"})
        self.assertEqual(
            imported,
            {"lockdown_mode": "1", "read_only_mode": "0"},
        )
        self.settings.update(imported, None)
        self.assertEqual(self.settings.file_protection_mode(), "lockdown")


if __name__ == "__main__":
    unittest.main()
