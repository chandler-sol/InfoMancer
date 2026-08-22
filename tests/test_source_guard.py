import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("INFOMANCER_AUTH_MODE", "disabled")

import app.main as main
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