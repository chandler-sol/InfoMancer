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
        loader = (ROOT / "app/static/review.css").read_text(encoding="utf-8")
        self.assertIn('@import url("workspace-detail-polish.css?v=1")', loader)
        self.assertIn("flex-wrap: wrap", css)
        self.assertIn("flex: 1 1 128px", css)
        self.assertIn("flex-basis: 190px", css)
        self.assertIn(".dossier-on-disk .file-list > article > .grow", css)
        self.assertIn(".dossier-on-disk .season-heading", css)
        self.assertIn("max-width: 100%", css)
        self.assertIn("text-overflow: ellipsis", css)

    def test_selected_cover_frame_moves_with_card_and_matches_poster_shape(self):
        css = (ROOT / "app/static/workspace-detail-polish.css").read_text(encoding="utf-8")
        self.assertIn(".cover-card.workspace-selected::after", css)
        self.assertIn(".cover-card:hover {", css)
        self.assertIn("transform: translateY(-4px)", css)
        self.assertIn("border-radius: var(--im-radius-md) var(--im-radius-md) 0 0", css)
        self.assertIn(".cover-card:hover .cover-art", css)
        self.assertIn("transform: none", css)

    def test_inspector_quick_details_and_media_spacing_are_preserved(self):
        template = (ROOT / "app/templates/_workspace_inspector.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/workspace-detail-polish.css").read_text(encoding="utf-8")
        overview_pos = template.index("workspace-inspector-overview")
        quick_pos = template.index("workspace-inspector-quick-action")
        health_pos = template.index('aria-labelledby="inspector-health-title"')
        self.assertLess(overview_pos, quick_pos)
        self.assertLess(quick_pos, health_pos)
        self.assertEqual(template.count("Open full details"), 1)
        self.assertIn("workspace-inspector-quick-action > .button.primary", css)
        self.assertIn("width: auto", css)
        self.assertNotIn(".workspace-inspector-footer-actions { display: contents; }", css)
        self.assertIn("workspace-inspector-media-grid + .workspace-inspector-seasons", css)
        self.assertIn("margin-top: 13px", css)

    def test_native_desktop_open_path_requirement_is_documented(self):
        note = (ROOT / "desktop/NATIVE_FEATURES.md").read_text(encoding="utf-8")
        self.assertIn("Open Path", note)
        self.assertIn("File Explorer", note)
        self.assertIn("Finder", note)
        self.assertIn("reveal the exact indexed movie file", note)
        self.assertIn("org.freedesktop.FileManager1.ShowItems", note)
        self.assertIn("ordinary browser sessions", note)
        self.assertIn("remote InfoMancer server", note)

    def test_new_motion_respects_reduced_motion(self):
        css = (ROOT / "app/static/workspace-detail-polish.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn(".cover-card:hover { transform: none; }", css)
        self.assertIn(".workspace-inspector-season-chevron { transition: none; }", css)

    def test_recovery_confirmation_has_explicit_accessible_labels(self):
        page = (ROOT / "app/templates/recovery_restore.html").read_text(encoding="utf-8")
        preview = (ROOT / "app/templates/recovery_restore_preview.html").read_text(encoding="utf-8")
        self.assertIn("Recovery package", page)
        self.assertIn('name="recovery_file"', page)
        self.assertIn("Type <strong>RESTORE</strong> to continue", preview)
        self.assertIn('name="confirm"', preview)

    def test_recovery_upload_uses_csrf_header_so_multipart_can_stream(self):
        page = (ROOT / "app/templates/recovery_restore.html").read_text(encoding="utf-8")
        security = (ROOT / "app/request_security.py").read_text(encoding="utf-8")
        self.assertIn('headers: {"X-CSRF-Token": {{ csrf_token|tojson }}}', page)
        self.assertIn("body: new FormData(form)", page)
        self.assertIn("Multipart uploads and API requests must send X-CSRF-Token", security)
        self.assertIn('content_type.startswith("application/x-www-form-urlencoded")', security)


if __name__ == "__main__":
    unittest.main()
