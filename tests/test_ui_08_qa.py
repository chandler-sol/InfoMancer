from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"


class Ui08QaContracts(unittest.TestCase):
    def test_tv_inspector_is_lazy_and_accessible(self):
        template = (ROOT / "app/templates/_workspace_inspector.html").read_text(encoding="utf-8")
        core = (STATIC / "workspace-core.js").read_text(encoding="utf-8")
        self.assertIn("data-inspector-tv-seasons", template)
        self.assertIn("/inspector-media/${encodeURIComponent(season.key)}", core)
        self.assertIn('trigger.setAttribute("aria-expanded", String(opening))', core)
        self.assertIn('seasonBody.dataset.loaded === "true"', core)
        self.assertIn("requestController?.signal", core)

    def test_detail_layout_uses_consolidated_workspace_polish_without_import_waterfall(self):
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        loader = (STATIC / "workspace.js").read_text(encoding="utf-8")
        core = (STATIC / "workspace-core.js").read_text(encoding="utf-8")
        self.assertFalse((STATIC / "workspace-detail-polish.css").exists())
        self.assertNotIn("@import url(", css)
        self.assertIn("Consolidated 0.8 workspace/detail polish", css)
        self.assertNotIn("workspace-detail-polish.css", loader)
        self.assertNotIn("workspace-detail-polish.css", core)
        self.assertNotIn("workspaceDetailPolish", loader)
        self.assertNotIn("workspaceDetailPolish", core)
        self.assertIn("flex-wrap: wrap", css)
        self.assertIn("flex: 1 1 128px", css)
        self.assertIn("flex-basis: 190px", css)
        self.assertIn(".dossier-on-disk .file-list > article > .grow", css)
        self.assertIn(".dossier-on-disk .season-heading", css)
        self.assertIn("max-width: 100%", css)
        self.assertIn("text-overflow: ellipsis", css)

    def test_selected_cover_frame_moves_with_card_and_matches_poster_shape(self):
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        self.assertIn(".cover-card.workspace-selected::after", css)
        self.assertIn(".cover-card:hover {", css)
        self.assertIn("transform: translateY(-4px)", css)
        self.assertIn("border-radius: var(--im-radius-md) var(--im-radius-md) 0 0", css)
        self.assertIn(".cover-card:hover .cover-art", css)
        self.assertIn("transform: none", css)

    def test_library_readability_pass_uses_wider_canvas_and_larger_type(self):
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        self.assertIn("--text: #f5f7fa", css)
        self.assertIn("--muted: #9ba9b8", css)
        self.assertIn("font-size: 16px", css)
        self.assertIn("main.shell:has(> .catalog-tabs)", css)
        self.assertIn("max-width: 1700px", css)
        self.assertIn("max-width: 1840px", css)
        self.assertIn(".cover-card-link > strong", css)
        self.assertIn("font-size: 15px", css)
        self.assertIn(".cover-card-meta", css)
        self.assertIn("font-size: 12.5px", css)
        self.assertIn(".library-table .title-link", css)
        self.assertIn(".library-table td small", css)
        self.assertIn(".workspace-inspector-meta.compact dd", css)
        self.assertIn("font-size: 11.5px", css)

    def test_topbar_task_widget_replaces_sidebar_floating_card(self):
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        template = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
        self.assertIn('class="task-widget idle"', template)
        self.assertIn('class="topbar-actions"', template)
        self.assertIn("body.has-app-sidebar .topbar .task-widget", css)
        self.assertIn("position: relative", css)
        self.assertIn("flex: 0 0 42px", css)
        self.assertIn("width: 42px", css)
        self.assertIn("body.has-app-sidebar .topbar .task-popover", css)
        self.assertIn("top: calc(100% + 12px)", css)
        self.assertIn("right: 0", css)
        self.assertIn('content: "Tasks & notifications"', css)
        self.assertIn(".topbar #task-minimize", css)
        self.assertIn("display: none", css)

    def test_inspector_quick_details_and_media_spacing_are_preserved(self):
        template = (ROOT / "app/templates/_workspace_inspector.html").read_text(encoding="utf-8")
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        identity_pos = template.index('class="workspace-inspector-identity"')
        details_pos = template.index('<a class="button workspace-inspector-details-shortcut"')
        favorite_pos = template.index("workspace-inspector-favorite")
        overview_pos = template.index("workspace-inspector-overview")
        self.assertLess(identity_pos, details_pos)
        self.assertLess(details_pos, favorite_pos)
        self.assertLess(favorite_pos, overview_pos)
        self.assertEqual(template.count("Open full details"), 1)
        self.assertNotIn("workspace-inspector-summary-actions", template)
        self.assertIn('class="button workspace-inspector-details-shortcut"', template)
        self.assertIn("data-workspace-inspector-inline-polish", template)
        self.assertIn("workspace-inspector-details-shortcut", css)
        self.assertIn("border-radius: 999px", css)
        self.assertIn("background: var(--lime)", css)
        self.assertIn("color: #0b1009", css)
        self.assertIn("workspace-inspector-media-grid + .workspace-inspector-seasons", css)
        self.assertIn("margin-top: 13px", css)

    def test_inspector_file_code_column_is_centered(self):
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: 72px minmax(0, 1fr)", css)
        self.assertIn("justify-self: center", css)
        self.assertIn("text-align: center", css)

    def test_settings_metadata_queue_links_have_dark_theme_contrast(self):
        css = (STATIC / "review.css").read_text(encoding="utf-8")
        self.assertIn(".settings-table-wrap a:visited", css)
        self.assertIn("color: var(--text)", css)
        self.assertIn(".settings-table-wrap a:hover", css)
        self.assertIn("text-decoration-color: var(--lime)", css)

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
        css = (STATIC / "review.css").read_text(encoding="utf-8")
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
