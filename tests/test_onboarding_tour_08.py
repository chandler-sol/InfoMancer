from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
TEMPLATES = ROOT / "app" / "templates"


class OnboardingTour08ContractTests(unittest.TestCase):
    def test_tour_has_current_workspace_flow(self) -> None:
        source = (STATIC / "onboarding-tour.js").read_text(encoding="utf-8")
        step_ids = re.findall(r'id: "([^"]+)"', source)
        self.assertEqual(
            step_ids,
            [
                "welcome",
                "navigation",
                "library-scope",
                "filters",
                "display",
                "inspector",
                "review",
                "sources",
                "safety",
                "scheduled-tasks",
                "recovery",
                "operations",
                "tasks",
                "global-search",
                "profile",
            ],
        )
        for copy in (
            "Saved Views",
            "Inspector",
            "Review is your decision inbox",
            "persisted rename proposals",
            "Source Guard",
            "Read-Only Mode",
            "Scheduled Tasks",
            "Portable .infomancer-backup packages",
            "Safe Undo",
            "cancelled safely",
            "command palette",
            "recent searches",
        ):
            self.assertIn(copy, source)
        self.assertNotIn("Choose the library you need", source)
        self.assertNotIn("Never miss what changed", source)
        self.assertNotIn("announcement-heading", source)

    def test_librarian_only_steps_are_role_scoped(self) -> None:
        source = (STATIC / "onboarding-tour.js").read_text(encoding="utf-8")
        self.assertIn("...(isLibrarian ? [", source)
        for path in (
            'path: "/sources"',
            'path: "/settings/system"',
            'path: "/settings/scheduled-tasks"',
            'path: "/settings/recovery"',
            'path: "/operations"',
        ):
            self.assertIn(path, source)
        self.assertIn("] : []),", source)

    def test_tour_targets_exist_in_current_templates(self) -> None:
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        library = (TEMPLATES / "library.html").read_text(encoding="utf-8")
        review = (TEMPLATES / "review.html").read_text(encoding="utf-8")
        sources = (TEMPLATES / "sources.html").read_text(encoding="utf-8")
        settings = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
        scheduled = (TEMPLATES / "scheduled_tasks.html").read_text(encoding="utf-8")
        recovery = (TEMPLATES / "recovery_restore.html").read_text(encoding="utf-8")
        operations = (TEMPLATES / "operations.html").read_text(encoding="utf-8")

        for marker in (
            'id="site-menu-panel"',
            'class="global-search-toggle"',
            'id="task-widget"',
            'class="account-menu series-menu"',
        ):
            self.assertIn(marker, base)
        for marker in (
            'class="catalog-tabs"',
            'class="saved-view-bar"',
            'class="library-search library-controls"',
            'class="library-view-controls"',
            'class="tour-demo-list"',
        ):
            self.assertIn(marker, library)
        self.assertIn('class="review-summary-strip"', review)
        self.assertIn('class="panel add-root source-add-panel"', sources)
        self.assertIn('class="panel settings-card system-safety-card full-width" id="safety"', settings)
        self.assertIn('class="scheduled-task-layout"', scheduled)
        self.assertIn('id="recovery-upload-form"', recovery)
        self.assertIn('class="operation-history-summary"', operations)

    def test_engagement_runtime_delegates_tour_to_dedicated_owner(self) -> None:
        source = (STATIC / "engagement.js").read_text(encoding="utf-8")
        self.assertIn('loadStyle("onboarding-tour.css")', source)
        self.assertIn('loadStyle("onboarding-tour-inspector-preview.css")', source)
        self.assertIn('loadScript("onboarding-tour.js")', source)
        self.assertIn('loadScript("onboarding-tour-inspector-preview.js")', source)
        self.assertIn("tour.hidden = true", source)
        self.assertNotIn("const steps = [", source)
        self.assertIn('document.getElementById("announcement-popup")', source)

    def test_inspector_step_has_visual_preview_on_mobile_and_desktop(self) -> None:
        source = (STATIC / "onboarding-tour-inspector-preview.js").read_text(encoding="utf-8")
        css = (STATIC / "onboarding-tour-inspector-preview.css").read_text(encoding="utf-8")
        self.assertIn('step === "5"', source)
        self.assertIn('"Inspect first, act second"', source)
        self.assertIn('workspace-inspector tour-inspector-preview', source)
        self.assertIn('tour.classList.add("tour-inspector-active")', source)
        self.assertIn('preview.setAttribute("aria-hidden", "true")', source)
        self.assertIn("tour-inspector-preview-facts", source)
        self.assertNotIn("/library/inspector/", source)
        self.assertNotIn("workspaceInspectorTitleId", source)
        self.assertIn(".tour-layer.tour-inspector-active .tour-card", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("width: auto", css)
        self.assertIn("pointer-events: none", css)

    def test_mobile_tour_uses_stable_safe_area_bottom_sheet(self) -> None:
        css = (STATIC / "onboarding-tour.css").read_text(encoding="utf-8")
        self.assertIn("env(safe-area-inset-left)", css)
        self.assertIn("env(safe-area-inset-right)", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("max-height: min(46dvh, 410px)", css)
        self.assertIn("transition: none", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_tour_preserves_real_background_tasks(self) -> None:
        source = (STATIC / "onboarding-tour.js").read_text(encoding="utf-8")
        self.assertIn('!widget.classList.contains("idle")', source)
        self.assertIn("taskDemoSnapshot", source)
        self.assertIn("stopTaskDemo();", source)

    def test_tour_positioning_scrolls_targets_and_restores_library_view(self) -> None:
        source = (STATIC / "onboarding-tour.js").read_text(encoding="utf-8")
        self.assertIn("ensureTargetVisible", source)
        self.assertIn("window.scrollBy", source)
        self.assertIn("restoreLibraryView", source)
        self.assertIn("target.bottom + gap", source)
        self.assertIn("target.top - height - gap", source)

    def test_library_view_demo_waits_for_library_controller(self) -> None:
        source = (STATIC / "onboarding-tour.js").read_text(encoding="utf-8")
        self.assertIn("libraryControllerReady", source)
        self.assertIn('library-surface-lazy.js', source)
        self.assertIn("pendingLibraryView", source)
        self.assertIn('document.addEventListener("infomancer:library-view-changed"', source)
        self.assertIn("requestAnimationFrame(() => applyTourLibraryView(requested))", source)

    def test_failed_tour_state_save_restores_current_step(self) -> None:
        source = (STATIC / "onboarding-tour.js").read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r'catch \(error\) \{\s*window\.alert\(error\.message\);\s*render\(\);\s*return;',
        )


if __name__ == "__main__":
    unittest.main()
