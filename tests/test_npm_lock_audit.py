from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.audit_npm_lock import (
    NpmLockAuditError,
    _decode_registry_payload,
    audit_lockfile,
    blocking_advisories,
    dependency_versions,
    fetch_bulk_advisories,
)


class DummyHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class DummyResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = DummyHeaders(headers or {})

    def read(self, _limit: int = -1) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class NpmLockAuditTests(unittest.TestCase):
    def test_dependency_versions_reads_scoped_nested_and_optional_packages(self):
        lock = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "root", "version": "1.0.0"},
                "node_modules/@playwright/test": {"version": "1.2.3"},
                "node_modules/playwright": {"version": "1.2.3"},
                "node_modules/playwright/node_modules/@scope/helper": {
                    "version": "2.0.0",
                    "optional": True,
                },
            },
        }
        self.assertEqual(
            dependency_versions(lock),
            {
                "@playwright/test": ["1.2.3"],
                "@scope/helper": ["2.0.0"],
                "playwright": ["1.2.3"],
            },
        )

    def test_dependency_versions_rejects_missing_packages_map(self):
        with self.assertRaisesRegex(NpmLockAuditError, "packages map"):
            dependency_versions({"lockfileVersion": 1})

    def test_registry_payload_decodes_unlabelled_gzip(self):
        payload = gzip.compress(
            json.dumps(
                {
                    "playwright": [
                        {
                            "id": 123,
                            "severity": "high",
                            "title": "Example advisory",
                            "url": "https://example.test/advisory",
                        }
                    ]
                }
            ).encode("utf-8")
        )
        decoded = _decode_registry_payload(payload)
        self.assertEqual(decoded["playwright"][0]["severity"], "high")

    def test_blocking_advisories_respects_threshold(self):
        advisories = {
            "a": [{"severity": "moderate", "title": "moderate"}],
            "b": [{"severity": "high", "title": "high"}],
            "c": [{"severity": "critical", "title": "critical"}],
        }
        blocked = blocking_advisories(advisories, "high")
        self.assertEqual([package for package, _ in blocked], ["b", "c"])

    def test_unknown_advisory_severity_fails_closed(self):
        with self.assertRaisesRegex(NpmLockAuditError, "unknown severity"):
            blocking_advisories(
                {"playwright": [{"severity": "mystery"}]},
                "high",
            )

    def test_fetch_bulk_advisories_posts_version_map_and_handles_gzip_bug(self):
        response_body = gzip.compress(json.dumps({}).encode("utf-8"))
        response = DummyResponse(response_body)
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            captured["timeout"] = timeout
            return response

        with patch("scripts.audit_npm_lock.urllib.request.urlopen", side_effect=fake_urlopen):
            result = fetch_bulk_advisories({"playwright": ["1.2.3"]}, timeout=4)

        self.assertEqual(result, {})
        request = captured["request"]
        self.assertEqual(
            request.full_url,
            "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk",
        )
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"playwright": ["1.2.3"]},
        )
        self.assertLessEqual(captured["timeout"], 30.0)

    def test_audit_lockfile_reports_high_advisory(self):
        with tempfile.TemporaryDirectory() as temporary:
            lockfile = Path(temporary) / "package-lock.json"
            lockfile.write_text(
                json.dumps(
                    {
                        "lockfileVersion": 3,
                        "packages": {
                            "": {"name": "root", "version": "1.0.0"},
                            "node_modules/playwright": {"version": "1.2.3"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "scripts.audit_npm_lock.fetch_bulk_advisories",
                return_value={
                    "playwright": [
                        {
                            "severity": "high",
                            "title": "Example advisory",
                            "url": "https://example.test/advisory",
                        }
                    ]
                },
            ):
                blocked = audit_lockfile(lockfile, "high")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0][0], "playwright")


if __name__ == "__main__":
    unittest.main()
