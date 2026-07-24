import tempfile
import unittest
from pathlib import Path

from app.app_settings import AppSettingError, AppSettings
from app.db import Database


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "settings.db")
        self.database.initialize()
        self.settings = AppSettings(
            self.database, "https://example.test/search?q={query}"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_defaults_validation_update_and_history(self):
        self.assertEqual(self.settings.get("installation_name"), "InfoMancer")
        self.assertEqual(self.settings.get("search_provider_name"), "example.test")

        values = self.settings.validate_general(
            "  Family   Archive  ", "America/New_York", "covers", "220"
        )
        changed = self.settings.update(values, None)
        self.assertEqual(changed, 4)
        self.assertEqual(self.settings.get("installation_name"), "Family Archive")
        self.assertEqual(self.settings.get("default_cover_size"), "220")
        self.assertEqual(len(self.settings.history()), 4)

        self.assertEqual(self.settings.update(values, None), 0)
        self.assertEqual(len(self.settings.history()), 4)

    def test_plain_language_validation_errors(self):
        with self.assertRaisesRegex(AppSettingError, "recognized IANA time zone"):
            self.settings.validate_general("Archive", "Somewhere/Nowhere", "list", "180")
        with self.assertRaisesRegex(AppSettingError, "between 120 and 300"):
            self.settings.validate_general("Archive", "UTC", "list", "500")
        with self.assertRaisesRegex(AppSettingError, "exactly one"):
            self.settings.validate_external_search(
                "Example", "https://example.test/search"
            )
        with self.assertRaisesRegex(AppSettingError, "complete HTTP or HTTPS"):
            self.settings.validate_external_search("Example", "ftp://example.test/{query}")

    def test_external_search_update(self):
        values = self.settings.validate_external_search(
            "Search Site", "https://search.example/find/{query}"
        )
        self.assertEqual(self.settings.update(values, None), 2)
        self.assertEqual(self.settings.get("search_provider_name"), "Search Site")
        self.assertEqual(
            self.settings.get("search_url_template"),
            "https://search.example/find/{query}",
        )

    def test_logging_levels_are_explicit_and_persisted(self):
        self.assertEqual(
            self.settings.validate_logging("Verbose"), {"log_level": "verbose"}
        )
        self.assertEqual(
            self.settings.update({"log_level": "verbose"}, None), 1
        )
        self.assertEqual(self.settings.get("log_level"), "verbose")
        with self.assertRaisesRegex(AppSettingError, "Standard, Verbose, or Debug"):
            self.settings.validate_logging("everything")


if __name__ == "__main__":
    unittest.main()
