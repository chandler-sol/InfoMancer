from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProfileLibraryPolishContracts(unittest.TestCase):
    def test_operational_dashboard_uses_optical_inset_and_six_recent_titles(self):
        template = (ROOT / "app/templates/dashboard_command.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/dashboard-command.css").read_text(encoding="utf-8")

        self.assertIn("recent[:6]", template)
        self.assertIn(".home-ops-summary", css)
        self.assertIn("padding-left:5px", css)

    def test_repeated_library_title_click_toggles_inspector_closed(self):
        script = (ROOT / "app/static/workspace.js").read_text(encoding="utf-8")

        repeated = 'if (String(titleId) === selectedTitleId)'
        self.assertIn(repeated, script)
        self.assertIn("closeInspector({historyMode:", script)
        self.assertLess(script.index('if (event.metaKey || event.ctrlKey)'), script.index(repeated))
        self.assertLess(script.index('if (event.shiftKey)'), script.index(repeated))

    def test_library_display_controls_are_compact_and_first_paint_is_guarded(self):
        css = (ROOT / "app/static/modern.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/workspace.js").read_text(encoding="utf-8")

        self.assertIn(".library-view-controls", css)
        self.assertIn("max-width: min(100%, 430px)", css)
        self.assertIn(".cover-size-control:not([hidden])", css)
        self.assertIn("width: 220px", css)
        self.assertIn(".cover-library", css)
        self.assertIn("justify-content: start", css)
        self.assertIn("library-first-paint-fallback", css)
        self.assertIn('classList.add("library-view-ready")', script)

    def test_profile_page_has_live_visual_picker_and_custom_upload_contract(self):
        template = (ROOT / "app/templates/account_profile.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/profile.css").read_text(encoding="utf-8")
        script = (ROOT / "app/static/profile.js").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/account_avatar.py").read_text(encoding="utf-8")
        routes_init = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")

        self.assertIn("PROFILE PREVIEW", template)
        self.assertIn("data-profile-avatar-open", template)
        self.assertIn("PNG, JPEG, WebP", template)
        self.assertIn("128 × 128 minimum", template)
        self.assertIn("512 × 512 recommended", template)
        self.assertIn("2 MB maximum", template)
        self.assertIn("SVG is not accepted", template)
        self.assertIn("profile-primary-grid", css)
        self.assertIn("profile-preview-card", css)
        self.assertIn('createImageBitmap(file)', script)
        self.assertIn('canvas.toBlob(resolve, "image/png")', script)
        self.assertIn('"Content-Type": "image/png"', script)
        self.assertIn('"X-CSRF-Token": csrf', script)
        self.assertIn("MAX_AVATAR_BYTES = 2 * 1024 * 1024", route)
        self.assertIn("width != AVATAR_EDGE or height != AVATAR_EDGE", route)
        self.assertIn("zlib.decompress", route)
        self.assertIn("os.replace", route)
        self.assertIn("build_account_avatar_router", routes_init)

    def test_account_avatar_surface_uses_authenticated_avatar_endpoint(self):
        css = (ROOT / "app/static/auth.css").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/account_avatar.py").read_text(encoding="utf-8")

        self.assertIn("background-image:url('/account/avatar/current')", css)
        self.assertIn('@router.get("/account/avatar/current")', route)
        self.assertIn('media_type="image/png"', route)
        self.assertIn('media_type="image/svg+xml"', route)
        self.assertIn('"Cache-Control": "private, no-store"', route)


if __name__ == "__main__":
    unittest.main()
