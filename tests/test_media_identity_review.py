from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

from app import main
from app.db import Database
from app.duplicates import DuplicateService
from app.mie import MediaIntelligenceEngine
from app.review_queue import ReviewQueue
from app.request_security import LOCAL_CSRF_COOKIE
from app.routes.health_action_routing import health_finding_href


ROOT = Path(__file__).resolve().parent.parent


class EpisodeIdentityReviewAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "review.db")
        self.database.initialize()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO roots(id,path,kind,label) VALUES (1,'/media/tv','tv','TV')"
            )
            conn.execute(
                """INSERT INTO titles(id,root_id,kind,title,folder_path)
                   VALUES (1,1,'tv','Example Show','/media/tv/Example Show')"""
            )
            conn.execute(
                """INSERT INTO files(
                     id,title_id,path,filename,extension,size_bytes,modified_at,
                     season,episode_start,episode_end,parsed_title,seen_scan
                   ) VALUES (
                     1,1,'/media/tv/Example Show/S01E01.mkv','S01E01.mkv',
                     'mkv',100,1,1,1,1,'Example Show','scan'
                   )"""
            )
            conn.execute(
                """INSERT INTO mie_findings(
                     id,fingerprint,rule_key,category,severity,root_id,title_id,
                     file_id,summary,explanation,recommendation,evidence_json,
                     status,first_seen_at,last_seen_at
                   ) VALUES (
                     1,'episode-identity:file:1:fixture','episode-identity-review',
                     'identity','warning',1,1,1,
                     'S01E01.mkv: content may be S01E02',
                     'Independent evidence favors another episode.',
                     'Review the evidence before changing anything.',
                     ?,'active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
                   )""",
                (
                    json.dumps({
                        "scan_id": 42,
                        "result_state": "likely_mismatch",
                        "confirmation": "none",
                        "best_candidate_support": "Strong",
                        "candidate_separation": "Meaningful",
                    }),
                ),
            )
        self.queue = ReviewQueue(
            self.database,
            MediaIntelligenceEngine(self.database),
            DuplicateService(self.database),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_episode_identity_finding_uses_matching_bucket_and_scan_drawer_data(self) -> None:
        item = self.queue.get_item("finding", "1", include_librarian=True)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["bucket"], "matching")
        self.assertEqual(item["source_label"], "Episode Identity")
        self.assertEqual(item["identity_scan_id"], 42)
        self.assertEqual(item["identity_state"], "likely_mismatch")
        self.assertEqual(item["review_label"], "Review episode identity")
        self.assertTrue(any(
            row["label"] == "Best Candidate Support" and row["value"] == "Strong"
            for row in item["evidence_rows"]
        ))

    def test_episode_identity_feedback_scope_is_forced_server_side(self) -> None:
        engine = MediaIntelligenceEngine(self.database)

        self.assertTrue(
            engine.dismiss(1, None, reason="expected", scope="title")
        )
        with self.database.connect() as conn:
            first = conn.execute(
                """SELECT scope FROM mie_feedback
                   WHERE finding_fingerprint='episode-identity:file:1:fixture'
                     AND active=1
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        self.assertEqual(first["scope"], "finding")

        self.assertTrue(engine.restore(1))
        self.assertTrue(
            engine.dismiss(1, None, reason="incorrect", scope="source")
        )
        with self.database.connect() as conn:
            second = conn.execute(
                """SELECT scope FROM mie_feedback
                   WHERE finding_fingerprint='episode-identity:file:1:fixture'
                     AND active=1
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        self.assertEqual(second["scope"], "finding")

    def test_health_action_routes_identity_finding_to_exact_scan(self) -> None:
        self.assertEqual(
            health_finding_href({
                "rule_key": "episode-identity-review",
                "title_id": 1,
                "root_id": 1,
                "evidence": {"scan_id": 42},
            }),
            "/episode-identity/scans/42",
        )


class EpisodeIdentityHttpBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "http-binding.db")
        self.database.initialize()
        self.original_db = main.db
        main.db = self.database
        self.auth_patch = patch.object(
            main, "settings", replace(main.settings, auth_mode="disabled")
        )
        self.auth_patch.start()
        self.client = TestClient(main.app, follow_redirects=False)
        self.client.get("/")
        csrf_token = self.client.cookies.get(LOCAL_CSRF_COOKIE)
        self.assertTrue(csrf_token)
        self.client.headers.update({"X-CSRF-Token": csrf_token})

    def tearDown(self) -> None:
        self.client.close()
        self.auth_patch.stop()
        main.db = self.original_db
        self.temporary.cleanup()

    def test_all_episode_identity_routes_receive_request_from_fastapi(self) -> None:
        requests = [
            ("POST", "/files/999999/episode-identity/fast"),
            ("GET", "/episode-identity/scans/999999"),
            ("GET", "/episode-identity/scans/999999/rename-preview"),
            ("POST", "/episode-identity/scans/999999/confirm-current"),
            ("POST", "/episode-identity/scans/999999/confirm-best"),
        ]
        responses = []
        for method, url in requests:
            response = self.client.request(method, url)
            responses.append(response)
            self.assertNotEqual(
                response.status_code,
                422,
                f"{method} {url} treated request as a query parameter: {response.text}",
            )
        self.assertEqual([response.status_code for response in responses[:3]], [404, 404, 404])
        self.assertEqual([response.status_code for response in responses[3:]], [303, 303])

        target_paths = {
            "/files/{file_id}/episode-identity/fast",
            "/episode-identity/scans/{scan_id}",
            "/episode-identity/scans/{scan_id}/rename-preview",
            "/episode-identity/scans/{scan_id}/confirm-current",
            "/episode-identity/scans/{scan_id}/confirm-best",
        }
        checked = set()
        for route in main.app.routes:
            if getattr(route, "path", None) not in target_paths:
                continue
            checked.add(route.path)
            query_names = {
                parameter.name
                for parameter in getattr(route, "dependant").query_params
            }
            self.assertNotIn("request", query_names, route.path)
        self.assertEqual(checked, target_paths)


class EpisodeIdentityReviewContractTests(unittest.TestCase):
    def test_templates_and_routes_expose_safe_identity_actions(self) -> None:
        routes = (
            ROOT / "app" / "routes" / "episode_identity_review.py"
        ).read_text(encoding="utf-8")
        drawer = (
            ROOT / "app" / "templates" / "_review_drawer.html"
        ).read_text(encoding="utf-8")
        detail = (
            ROOT / "app" / "templates" / "detail.html"
        ).read_text(encoding="utf-8")
        rename = (
            ROOT / "app" / "templates" / "episode_identity_rename_preview.html"
        ).read_text(encoding="utf-8")
        identity = (
            ROOT / "app" / "templates" / "episode_identity.html"
        ).read_text(encoding="utf-8")
        health = (
            ROOT / "app" / "templates" / "library_health.html"
        ).read_text(encoding="utf-8")

        self.assertIn('/files/{file_id}/episode-identity/fast', routes)
        self.assertIn('/episode-identity/scans/{scan_id}', routes)
        self.assertIn("@librarian_get(", routes)
        self.assertNotIn("resolve_if_needed=True", routes)
        self.assertIn('/confirm-current', routes)
        self.assertIn('/confirm-best', routes)
        self.assertIn("MediaIdentityDecisionService", routes)
        self.assertIn("except sqlite3.Error as exc", routes)
        self.assertIn("Library Health will catch up", routes)
        self.assertIn("Mark current filename correct", drawer)
        self.assertIn("Confirm suggested content", drawer)
        self.assertIn("Preview rename suggestion", drawer)
        self.assertIn("Verify Episode Identity", detail)
        self.assertIn("read-only", rename.casefold())
        self.assertIn("identity.snapshot_current", identity)
        self.assertIn("finding.rule_key == 'episode-identity-review'", health)
        self.assertIn('name="scope" value="finding"', health)
        self.assertNotIn("source.rename(", routes)
        self.assertNotIn("destination.rename(", routes)

    def test_identity_templates_compile(self) -> None:
        environment = Environment(
            loader=FileSystemLoader(ROOT / "app" / "templates")
        )
        environment.get_template("episode_identity.html")
        environment.get_template("episode_identity_rename_preview.html")


if __name__ == "__main__":
    unittest.main()
