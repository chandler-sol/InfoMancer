import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

from app import main


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = patch.object(
            main,
            "settings",
            SimpleNamespace(search_url_template="https://ext.to/browse/?q={query}"),
        )
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()

    def test_episode_query_is_url_encoded(self):
        self.assertEqual(
            main.provider_search_url("House of the Dragon S03E04"),
            "https://ext.to/browse/?q=House+of+the+Dragon+S03E04",
        )

    def test_ext_series_search_prefers_imdb_and_newest_sort(self):
        title = {
            "imdb_id": "tt11198330",
            "metadata_title": "House of the Dragon",
            "title": "House of the Dragon",
        }
        self.assertEqual(
            main.series_provider_search_url(title),
            "https://ext.to/browse/?imdb_id=tt11198330&order=desc&sort=age",
        )

    def test_series_search_falls_back_to_title_query(self):
        title = {"imdb_id": None, "metadata_title": "1923", "title": "1923"}
        self.assertEqual(
            main.series_provider_search_url(title),
            "https://ext.to/browse/?q=1923",
        )


if __name__ == "__main__":
    unittest.main()
