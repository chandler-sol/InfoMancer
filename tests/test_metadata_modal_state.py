from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MetadataModalStateTests(unittest.TestCase):
    def test_scope_switching_keeps_cached_views_instead_of_rebuilding_dialog(self):
        source = (ROOT / "app/static/metadata-maintenance.js").read_text(encoding="utf-8")
        css = (ROOT / "app/static/metadata-maintenance.css").read_text(encoding="utf-8")

        self.assertIn("const scopeCache = new Map", source)
        self.assertIn("scrollTop: 0", source)
        self.assertIn("loaded: false", source)
        self.assertIn("promise: null", source)
        self.assertIn("height:min(78vh,760px)", css)
        self.assertIn("height:100%;min-height:0", css)
        self.assertIn("overscroll-behavior:contain", css)

    def test_title_refresh_state_survives_switching_metadata_views(self):
        source = (ROOT / "app/static/metadata-maintenance.js").read_text(encoding="utf-8")

        self.assertIn("const refreshJobs = new Map()", source)
        self.assertIn("const applyJobToRow =", source)
        self.assertIn("const updateVisibleJob =", source)
        self.assertIn("refreshJobs.get(titleId)", source)
        self.assertNotIn("if (!row.isConnected) return;", source)

    def test_dialog_close_buttons_use_shared_optical_centering(self):
        source = (ROOT / "app/static/dialog-controls.css").read_text(encoding="utf-8")

        self.assertIn(".metadata-maintenance-close,", source)
        self.assertIn(".tvdb-credentials-close,", source)
        self.assertIn("place-items: center", source)
        self.assertIn("transform: translate(-50%, -50%) rotate(45deg)", source)
        self.assertIn("transform: translate(-50%, -50%) rotate(-45deg)", source)
        self.assertNotIn('content: "×"', source)


if __name__ == "__main__":
    unittest.main()
