from __future__ import annotations

import urllib.error
import urllib.request
import unittest

from app.routes.update_channel_settings import (
    _HttpsOnlyRedirectHandler,
    _require_https_update_url,
)


class UpdateTransportSecurityTests(unittest.TestCase):
    def test_plain_http_update_metadata_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            _require_https_update_url("http://updates.example.invalid/dev.json")

    def test_update_metadata_url_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            _require_https_update_url(
                "https://user:password@updates.example.invalid/dev.json"
            )

    def test_https_update_metadata_url_is_allowed(self):
        _require_https_update_url("https://updates.example.invalid/dev.json")

    def test_redirect_handler_rejects_https_to_http_downgrade(self):
        handler = _HttpsOnlyRedirectHandler()
        request = urllib.request.Request("https://updates.example.invalid/dev.json")
        with self.assertRaisesRegex(urllib.error.URLError, "remain on HTTPS"):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://mirror.example.invalid/dev.json",
            )

    def test_redirect_handler_allows_https_redirect(self):
        handler = _HttpsOnlyRedirectHandler()
        request = urllib.request.Request("https://updates.example.invalid/dev.json")
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://cdn.example.invalid/dev.json",
        )
        self.assertEqual(redirected.full_url, "https://cdn.example.invalid/dev.json")


if __name__ == "__main__":
    unittest.main()
