from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"


class ProfileLibraryPolishContracts(unittest.TestCase):
    def test_operational_dashboard_uses_optical_inset_and_six_recent_titles(self):
        template = (ROOT / "app/templates/dashboard_command.html").read_text(encoding="utf-8")
        css = (STATIC / "dashboard-command.css").read_text(encoding="utf-8")
        self.assertIn("recent[:6]", template)
        self.assertIn(".home-ops-summary", css)
        self.assertIn("padding-left:5px", css)

    def test_repeated_library_title_click_toggles_inspector_closed(self):
        core = (STATIC / "workspace-core.js").read_text(encoding="utf-8")
        repeated = 'if (String(titleId) === selectedTitleId)'
        self.assertIn(repeated, core)
        self.assertIn("closeInspector({historyMode:", core)
        self.assertLess(core.index('if (event.metaKey || event.ctrlKey)'), core.index(repeated))
        self.assertLess(core.index('if (event.shiftKey)'), core.index(repeated))

    def test_library_display_controls_use_semantic_density_and_guard_first_paint(self):
        density = (STATIC / "library-density.css").read_text(encoding="utf-8")
        loader = (STATIC / "workspace-ui.js").read_text(encoding="utf-8")
        self.assertIn("grid-template-columns: repeat(5, 34px)", density)
        self.assertIn("grid-template-columns: repeat(3, minmax(0, 1fr))", density)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", density)
        self.assertIn('#cover-library[data-mobile-density="spacious"]', density)
        self.assertIn(".cover-size-value", density)
        self.assertIn("display: none !important", density)
        self.assertIn("coverSizeControl.style.visibility = 'hidden'", loader)
        self.assertIn("libraryViewToolbar.style.visibility = 'hidden'", loader)
        self.assertIn("'library-density.js'", loader)

    def test_cross_document_navigation_keeps_app_chrome_stable(self):
        css = (STATIC / "modern.css").read_text(encoding="utf-8")
        stable = (STATIC / "navigation-paint-stability.css").read_text(encoding="utf-8")
        self.assertIn("@view-transition", css)
        self.assertIn("navigation: auto", css)
        self.assertIn("view-transition-name: infomancer-chrome", css)
        # Branches may differ on whether modern.css still carries the retired named
        # content rule. The release-owned final layer must neutralize it either way.
        self.assertIn("main.shell,", stable)
        self.assertIn("view-transition-name: none !important", stable)
        self.assertIn("::view-transition-old(root)", stable)
        self.assertIn("::view-transition-new(root)", stable)
        self.assertIn("infomancer-root-reveal", stable)
        self.assertIn("content-visibility: auto", css)
        self.assertIn("contain-intrinsic-size: 280px 420px", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)

    def test_desktop_density_pass_reclaims_vertical_space(self):
        css = (STATIC / "modern.css").read_text(encoding="utf-8")
        self.assertIn("Desktop density pass", css)
        self.assertIn("@media (min-width: 981px)", css)
        self.assertIn("body.has-app-sidebar .shell", css)
        self.assertIn("main.shell:has(> .home-ops)", css)
        self.assertIn("body.has-app-sidebar .catalog-tabs", css)
        self.assertIn("body.has-app-sidebar .settings-section-nav", css)
        self.assertIn("body.has-app-sidebar .profile-page", css)

    def test_profile_page_has_live_visual_picker_and_custom_upload_contract(self):
        template = (ROOT / "app/templates/account_profile.html").read_text(encoding="utf-8")
        modern = (STATIC / "modern.css").read_text(encoding="utf-8")
        script = (STATIC / "profile.js").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/account_avatar.py").read_text(encoding="utf-8")
        routes_init = (ROOT / "app/routes/__init__.py").read_text(encoding="utf-8")

        self.assertIn("<h1>Profile Settings</h1>", template)
        self.assertNotIn("PROFILE PREVIEW", template)
        self.assertIn("data-profile-avatar-open", template)
        self.assertIn("PNG, JPEG, WebP", template)
        self.assertIn("128 × 128 minimum", template)
        self.assertIn("512 × 512 recommended", template)
        self.assertIn("2 MB maximum", template)
        self.assertIn("SVG is not accepted", template)
        self.assertIn("{% macro profile_icon_svg(icon)", template)
        self.assertIn('data-profile-icon-choice="{{ icon }}"', template)
        self.assertIn("<strong>Custom Icon</strong>", template)
        self.assertIn("profile-custom-plus", template)
        self.assertIn(".profile-icon-glyph svg", modern)
        self.assertIn("stroke-linecap: round", modern)
        self.assertIn('createImageBitmap(file)', script)
        self.assertIn('canvas.toBlob(resolve, "image/png")', script)
        self.assertIn('"Content-Type": "image/png"', script)
        self.assertIn('"X-CSRF-Token": csrf', script)
        self.assertIn("choiceSvg(choice)", script)
        self.assertIn("icon.cloneNode(true)", script)
        self.assertIn("MAX_AVATAR_BYTES = 2 * 1024 * 1024", route)
        self.assertIn("PROFILE_ICON_SVGS", route)
        self.assertIn('viewBox="0 0 24 24"', route)
        self.assertIn("width != AVATAR_EDGE or height != AVATAR_EDGE", route)
        self.assertIn("zlib.decompress", route)
        self.assertIn("os.replace", route)
        self.assertIn("build_account_avatar_router", routes_init)

    def test_account_avatar_surface_has_one_stable_network_owner(self):
        auth_css = (STATIC / "auth.css").read_text(encoding="utf-8")
        loader = (STATIC / "workspace-ui.js").read_text(encoding="utf-8")
        route = (ROOT / "app/routes/account_avatar.py").read_text(encoding="utf-8")
        self.assertNotIn("background-image:url('/account/avatar/current')", auth_css)
        self.assertIn("Avoiding a CSS background request", auth_css)
        self.assertIn("avatarImage.src = '/account/avatar/current'", loader)
        self.assertNotIn("avatarImage.src = `/account/avatar/current?v=${Date.now()}`", loader)
        self.assertIn('@router.get("/account/avatar/current")', route)
        self.assertIn('AVATAR_CACHE_CONTROL = "private, no-cache"', route)
        self.assertIn('"ETag": etag', route)
        self.assertIn("_etag_matches", route)


if __name__ == "__main__":
    unittest.main()
