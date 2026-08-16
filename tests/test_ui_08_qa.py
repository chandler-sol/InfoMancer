from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Ui08QaContracts(unittest.TestCase):
    def test_tv_inspector_is_lazy_and_accessible(self):
        template = (ROOT / "app/templates/_workspace_inspector.html").read_text(encoding="utf-8")
        script = (ROOT / "app/static/workspace.js").read_text(encoding="utf-8")
        self.assertIn("data-inspector-tv-seasons", template)
        self.assertIn("/inspector-media/${encodeURIComponent(season.key)}", script)
        self.assertIn('trigger.setAttribute("aria-expanded", String(opening))', script)
        self.assertIn('seasonBody.dataset.loaded === "true"', script)
        self.assertIn("requestController?.signal", script)

    def test_detail_layout_explicitly_contains_long_rows_and_technical_rail(self):
        css = (ROOT / "app/static/workspace-detail-polish.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(140px, 1fr))", css)
        self.assertIn(".dossier-on-disk .file-list > article > .grow", css)
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn(".dossier-on-disk .season-heading", css)
        self.assertIn("max-width: 100%", css)

    def test_new_motion_respects_reduced_motion(self):
        css = (ROOT / "app/static/workspace-detail-polish.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn(".workspace-inspector-season-chevron { transition: none; }", css)

    def test_recovery_confirmation_has_explicit_accessible_labels(self):
        page = (ROOT / "app/templates/recovery_restore.html").read_text(encoding="utf-8")
        preview = (ROOT / "app/templates/recovery_restore_preview.html").read_text(encoding="utf-8")
        self.assertIn("Recovery package", page)
        self.assertIn('name="recovery_file"', page)
        self.assertIn("Type <strong>RESTORE</strong> to continue", preview)
        self.assertIn('name="confirm"', preview)


if __name__ == "__main__":
    unittest.main()
