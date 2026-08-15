from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.duplicates import DuplicateService
from app.mie import MediaIntelligenceEngine
from app.review_queue import ReviewQueue


ROOT = Path(__file__).resolve().parent.parent


class ReviewQueueServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tempdir.name) / "review.db")
        self.db.initialize()
        with self.db.connect() as conn:
            conn.execute("INSERT INTO roots(id,path,kind,label,enabled) VALUES (1,'/media/movies','movie','Movies',1)")
            conn.execute("""INSERT INTO titles(id,root_id,kind,title,folder_path,updated_at)
                            VALUES (1,1,'movie','Example Movie','/media/movies/Example Movie',CURRENT_TIMESTAMP)""")
            conn.execute("""INSERT INTO files(id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan)
                            VALUES (1,1,'/media/movies/Example Movie/a.mkv','a.mkv','mkv',1000,1,'scan')""")
            conn.execute("""INSERT INTO files(id,title_id,path,filename,extension,size_bytes,modified_at,seen_scan)
                            VALUES (2,1,'/media/movies/Example Movie/b.mkv','b.mkv','mkv',1000,1,'scan')""")
            conn.execute("""INSERT INTO mie_findings(
                         id,fingerprint,rule_key,category,severity,root_id,title_id,
                         summary,explanation,recommendation,evidence_json,status,
                         first_seen_at,last_seen_at)
                       VALUES (1,'test:1','metadata-identifiers-missing','identity','warning',1,1,
                         'Example Movie has no provider identifier','No provider ID is saved.',
                         'Review a provider match.','{}','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
            conn.execute("""INSERT INTO metadata_refresh_queue(
                         title_id,status,provider,error,requested_at,completed_at)
                       VALUES (1,'failed','tvdb','Provider unavailable',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
        self.queue = ReviewQueue(self.db, MediaIntelligenceEngine(self.db), DuplicateService(self.db))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_librarian_queue_unifies_mie_metadata_and_duplicates(self):
        view = self.queue.view(include_librarian=True)
        sources = {item["source"] for item in view["items"]}
        self.assertIn("finding", sources)
        self.assertIn("metadata", sources)
        self.assertIn("duplicate", sources)
        self.assertGreaterEqual(view["counts"]["warning"], 2)
        self.assertGreaterEqual(view["bucket_counts"]["duplicates"], 1)

    def test_member_queue_excludes_duplicate_cleanup(self):
        view = self.queue.view(include_librarian=False)
        self.assertNotIn("duplicate", {item["source"] for item in view["items"]})
        self.assertTrue(any(item["bucket"] == "matching" for item in view["items"]))

    def test_queue_filters_and_drawer_lookup(self):
        view = self.queue.view(bucket="matching", severity="warning", q="example", include_librarian=True)
        self.assertEqual(view["visible_count"], 1)
        finding = self.queue.get_item("finding", "1", include_librarian=True)
        self.assertIsNotNone(finding)
        self.assertEqual(finding["bucket"], "matching")
        duplicate = next(item for item in self.queue.view(include_librarian=True)["items"] if item["source"] == "duplicate")
        pair = duplicate["item_id"]
        self.assertIsNotNone(self.queue.get_item("duplicate", pair, include_librarian=True))
        self.assertIsNone(self.queue.get_item("duplicate", pair, include_librarian=False))


class ReviewWorkspaceContractTests(unittest.TestCase):
    def test_review_workspace_and_w4_assets_are_wired(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        routes = (ROOT / "app" / "routes" / "review.py").read_text(encoding="utf-8")
        template = (ROOT / "app" / "templates" / "review.html").read_text(encoding="utf-8")
        drawer = (ROOT / "app" / "templates" / "_review_drawer.html").read_text(encoding="utf-8")
        ui = (ROOT / "app" / "static" / "workspace-ui.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace-ui.css").read_text(encoding="utf-8")
        self.assertIn("path='workspace-ui.css'", base)
        self.assertIn("path='review.css'", base)
        self.assertIn("path='workspace-ui.js'", base)
        self.assertIn('href="/review" title="Review"', base)
        self.assertIn('@router.get("/review"', routes)
        self.assertIn('/review/items/{source}/{item_id}', routes)
        self.assertIn('/api/review/findings/{finding_id}/dismiss', routes)
        self.assertIn('/api/review/duplicates/{file_a_id}/{file_b_id}/decision', routes)
        self.assertIn("data-review-queue", template)
        self.assertIn("data-workspace-drawer-url", template)
        self.assertIn("data-workspace-ajax", drawer)
        self.assertIn("Workspace.toast", ui)
        self.assertIn("Workspace.confirm", ui)
        self.assertIn("workspace-command-palette", ui)
        self.assertIn("data-workspace-menu-toggle", ui)
        self.assertIn("workspace-drawer", styles)
        self.assertIn("top: 50%", styles)


if __name__ == "__main__":
    unittest.main()
