import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
import app.scanner as scanner
from app.db import Database
from app.mie import MediaIntelligenceEngine
from app.request_security import LOCAL_CSRF_COOKIE


class SourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "catalog.db")
        self.database.initialize()
        self.engine = MediaIntelligenceEngine(self.database)
        self.original = main.db, main.mie
        main.db, main.mie = self.database, self.engine

    def tearDown(self):
        main.db, main.mie = self.original
        self.temporary.cleanup()

    def add_root(self, path: Path, *, status: str = "unknown", baseline: int = 0) -> int:
        with self.database.connect() as conn:
            return conn.execute(
                """INSERT INTO roots(path,kind,label,health_status,last_file_count)
                   VALUES (?,'tv','NAS TV',?,?)""",
                (str(path), status, baseline),
            ).lastrowid

    def test_connection_check_marks_missing_mount_offline_without_catalog_cleanup(self):
        root_id = self.add_root(self.base / "missing-nas", baseline=42)

        result = main.check_source_health(root_id)

        self.assertEqual(result["status"], "offline")
        with self.database.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
        self.assertEqual(root["health_status"], "offline")
        self.assertEqual(root["last_file_count"], 42)
        self.assertIsNotNone(root["last_checked_at"])

    def test_reachable_degraded_source_reports_reachable_without_claiming_outage(self):
        source = self.base / "nas"
        source.mkdir()
        (source / "visible.mkv").write_bytes(b"visible")
        root_id = self.add_root(source, status="degraded", baseline=1)

        client = TestClient(main.app)
        sources = client.get("/sources")
        self.assertEqual(sources.status_code, 200)
        csrf_token = client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf_token)

        response = client.post(
            f"/roots/{root_id}/check",
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        query = parse_qs(urlparse(response.headers["location"]).query)
        message = query.get("message", [""])[0]
        self.assertIn("Connection confirmed", message)
        self.assertIn("source root is reachable", message)
        self.assertIn("complete scan", message)
        self.assertNotIn("unavailable or incomplete", message)

        with self.database.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
        # A quick connection check still must not clear Source Guard. Only a full
        # scan can prove the entire protected catalog is visible again.
        self.assertEqual(root["health_status"], "degraded")

    def test_full_accounted_scan_turns_unrelated_read_error_into_warning(self):
        source = self.base / "nas"
        source.mkdir()
        (source / "visible.mkv").write_bytes(b"visible")
        root_id = self.add_root(source)

        with self.database.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
            initial = scanner.scan_root(conn, root)
        self.assertEqual(initial["source_status"], "healthy")
        self.assertEqual(initial["files"], 1)

        original_walk = scanner._walk_files

        def walk_with_warning(root, errors):
            errors.append("Access denied while inspecting a non-media directory")
            yield from original_walk(root, errors)

        with patch.object(scanner, "_walk_files", walk_with_warning):
            with self.database.connect() as conn:
                root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
                result = scanner.scan_root(conn, root)

        self.assertEqual(result["source_status"], "healthy")
        self.assertEqual(result["read_errors"], 1)
        self.assertEqual(result["read_warnings"], 1)
        self.assertIn("Access denied", result["read_error_detail"])
        with self.database.connect() as conn:
            root = conn.execute("SELECT * FROM roots WHERE id=?", (root_id,)).fetchone()
        self.assertEqual(root["health_status"], "healthy")
        self.assertEqual(root["last_file_count"], 1)
        self.assertEqual(root["last_observed_file_count"], 1)
        self.assertEqual(root["guard_preserved_count"], 0)
        self.assertEqual(root["last_error"], "")

    def test_empty_reconnected_mount_is_degraded_and_gets_remediation_preview(self):
        source = self.base / "nas"
        source.mkdir()
        root_id = self.add_root(source, baseline=12)
        result = main.check_source_health(root_id)
        self.assertEqual(result["status"], "degraded")

        self.engine.analyze()
        finding = next(
            item for item in self.engine.findings()
            if item["rule_key"] == "source-degraded"
        )
        context = main.remediation_context(finding["id"])

        self.assertEqual({item["key"] for item in context["actions"]}, {"check", "reconcile"})
        self.assertIn("12", context["actions"][1]["changes"])

        client = TestClient(main.app)
        page = client.get(
            f"/library-health/findings/{finding['id']}/remediate"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn("Preview proposed action", page.text)
        self.assertIn("RECONCILE", page.text)

        sources = client.get("/sources")
        self.assertEqual(sources.status_code, 200)
        self.assertIn("Degraded", sources.text)
        self.assertIn("Source Guard is protecting", sources.text)

        csrf_token = client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf_token)
        batch = client.post(
            "/library-health/remediate-batch",
            data={"findings": str(finding["id"]), "action": "check_sources", "confirm": "CHECK"},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(batch.status_code, 303)
        self.assertIn("still+protected", batch.headers["location"])


if __name__ == "__main__":
    unittest.main()
