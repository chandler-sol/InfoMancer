from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.tvdb import BASE_URL, TVDBClient, TVDBError


class TVDBResilienceTests(unittest.TestCase):
    def test_persistent_unauthorized_response_retries_auth_only_once(self):
        client = TVDBClient("api-key", _token="stale-token")
        request = httpx.Request("GET", f"{BASE_URL}/search")
        unauthorized = httpx.Response(401, request=request, text="Unauthorized")
        login_request = httpx.Request("POST", f"{BASE_URL}/login")
        login = httpx.Response(
            200, request=login_request, json={"data": {"token": "fresh-token"}},
        )

        with patch("app.tvdb.httpx.get", side_effect=[unauthorized, unauthorized]) as get_mock, \
             patch("app.tvdb.httpx.post", return_value=login) as post_mock:
            with self.assertRaises(TVDBError):
                client.search_series("Example")

        self.assertEqual(get_mock.call_count, 2)
        self.assertEqual(post_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
