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
                "tasks",
                "global-search",
                "profile",
            ],
        )
        for copy in (
            "Saved Views",
            "Inspector",
            "Review is your decision inbox",
            "cancelled safely",
            "search history",
        ):
            self.assertIn(copy, source)
        self.assertNotIn("Choose the library you need", source)
        self.assertNotIn("Never miss what changed", source)
        self.assertNotIn("announcement-heading", source)

    def test_tour_targets_exist_in_current_templates(self) -> None:
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        library = (TEMPLATES / "library.html").read_text(encoding="utf-8")
        review = (TEMPLATES / "review.html").read_text(encoding="utf-8")

        for marker in (
            'id="site-menu-panel"',
            'class="global-search-toggle"',
            'id="task-widget"',
            'class="account-menu"',
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

    def test_engagement_runtime_delegates_tour_to_dedicated_owner(self) -> None:
        source = (STATIC / "engagement.js").read_text(encoding="utf-8")
        self.assertIn('loadStyle("onboarding-tour.css")', source)
        self.assertIn('loadScript("onboarding-tour.js")', source)
        self.assertIn("tour.hidden = true", source)
        self.assertNotIn("const steps = [", source)
        self.assertIn('document.getElementById("announcement-popup")', source)

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


if __name__ == "__main__":
    unittest.main()
