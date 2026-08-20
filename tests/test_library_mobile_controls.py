from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LibraryMobileControlTests(unittest.TestCase):
    def test_density_ui_hides_pixel_implementation_and_keeps_desktop_responsive(self):
        script = (ROOT / "app/static/library-density.js").read_text(encoding="utf-8")
        css = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")

        self.assertIn("Density", script)
        self.assertIn("Compact", script)
        self.assertIn("Balanced", script)
        self.assertIn("Spacious", script)
        self.assertIn("coverLibrary.style.setProperty('--cover-size'", script)
        self.assertIn(".cover-size-value", css)
        self.assertIn("display: none !important", css)
        self.assertNotIn("cover-size-output", script)

    def test_portrait_phone_density_has_exactly_three_meaningful_states(self):
        script = (ROOT / "app/static/library-density.js").read_text(encoding="utf-8")
        css = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")

        self.assertIn("(max-width: 600px)", script)
        self.assertIn("three covers across", script)
        self.assertIn("two covers across", script)
        self.assertIn("one large cover", script)
        self.assertIn('data-mobile-density="compact"', css)
        self.assertIn("repeat(3, minmax(0, 1fr))", css)
        self.assertIn('data-mobile-density="balanced"', css)
        self.assertIn("repeat(2, minmax(0, 1fr))", css)
        self.assertIn('data-mobile-density="spacious"', css)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", css)

    def test_general_settings_uses_density_names_while_preserving_server_value(self):
        script = (ROOT / "app/static/settings-cover-density.js").read_text(encoding="utf-8")
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn("Default cover density", script)
        self.assertIn("range.removeAttribute('name')", script)
        self.assertIn("hidden.name = fieldName", script)
        self.assertIn("hidden.value = String(step.size)", script)
        self.assertIn("output.value = step.name", script)
        self.assertIn("settingsCoverDensity", loader)
        self.assertIn('loadScript("settings-cover-density.js")', loader)

    def test_multi_selection_promotes_common_actions_and_collapses_secondary_work(self):
        script = (ROOT / "app/static/library-selection-toolbar.js").read_text(encoding="utf-8")
        css = (ROOT / "app/static/library-selection-compact.css").read_text(encoding="utf-8")

        self.assertIn("library-selection-summary", script)
        self.assertIn("library-selection-primary", script)
        self.assertIn("library-bulk-more-menu", script)
        self.assertIn("moreOptions.append(sortButton)", script)
        self.assertIn("moreOptions.append(refreshButton)", script)
        self.assertIn("moreOptions.append(matchMenu)", script)
        self.assertIn("deselectButton.textContent = 'Clear'", script)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr))", css)
        self.assertIn("min-height: 40px", css)

    def test_workspace_loader_requests_density_and_compact_selection_assets(self):
        loader = (ROOT / "app/static/workspace-ui.js").read_text(encoding="utf-8")

        self.assertIn('loadStyle("library-density.css")', loader)
        self.assertIn('loadStyle("library-selection-compact.css")', loader)
        self.assertIn('"library-density.js"', loader)
        self.assertLess(loader.index('"library-density.js"'), loader.index('"library-selection-toolbar.js"'))


if __name__ == "__main__":
    unittest.main()
