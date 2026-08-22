from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


class TitleDetailInspectorPolishTests(unittest.TestCase):
    def test_empty_movie_controls_do_not_create_on_disk_spacer(self):
        css = (STATIC / "mobile-detail.css").read_text(encoding="utf-8")
        self.assertIn(".dossier-on-disk .series-controls:not(:has(> *))", css)
        self.assertIn("display: none", css)

    def test_released_cleanup_is_movie_scoped(self):
        script = (STATIC / "title-catalog-cleanup.js").read_text(encoding="utf-8")
        self.assertIn(".media-dossier.detail-kind-movie", script)
        self.assertIn('=== "released"', script)
        self.assertIn("first.remove()", script)

    def test_inspector_actions_move_after_summary_in_dom(self):
        script = (STATIC / "inspector-quick-actions.js").read_text(encoding="utf-8")
        self.assertIn(".workspace-inspector-summary", script)
        self.assertIn(".workspace-inspector-footer-actions", script)
        self.assertIn("summary.after(actions)", script)
        self.assertIn("MutationObserver", script)

    def test_workspace_loads_both_polish_layers(self):
        script = (STATIC / "workspace.js").read_text(encoding="utf-8")
        self.assertIn('loadScript("inspector-quick-actions.js"', script)
        self.assertIn('loadScript("title-catalog-cleanup.js"', script)


if __name__ == "__main__":
    unittest.main()
