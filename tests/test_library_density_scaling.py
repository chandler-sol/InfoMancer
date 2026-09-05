from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LibraryDensityScalingTests(unittest.TestCase):
    def test_mobile_density_changes_card_footprint_not_only_column_count(self):
        styles = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")
        performance = (ROOT / "app/static/library-performance.css").read_text(encoding="utf-8")

        self.assertIn('data-mobile-density="compact"', styles)
        self.assertIn('data-mobile-density="balanced"', styles)
        self.assertIn('data-mobile-density="spacious"', styles)
        self.assertIn('grid-template-columns: repeat(3, minmax(0, 1fr));', styles)
        self.assertIn('grid-template-columns: repeat(2, minmax(0, 1fr));', styles)
        self.assertIn('grid-template-columns: minmax(0, 1fr);', styles)

        # Desktop performance deliberately caps cards at --cover-size. The phone
        # density layer must explicitly remove that cap or only the gutters move.
        self.assertIn('max-width: var(--cover-size);', performance)
        mobile_rule = styles.split('#cover-library[data-mobile-density] .cover-card {', 1)[1].split('}', 1)[0]
        self.assertIn('width: 100%;', mobile_rule)
        self.assertIn('max-width: none;', mobile_rule)
        self.assertIn('justify-self: stretch;', mobile_rule)

    def test_density_reveals_to_left_without_moving_view_toggle(self):
        styles = (ROOT / "app/static/library-density.css").read_text(encoding="utf-8")
        density = (ROOT / "app/static/library-density.js").read_text(encoding="utf-8")
        surface = (ROOT / "app/static/library-surface-lazy.js").read_text(encoding="utf-8")

        self.assertIn("viewControls.insertBefore(control, viewToggle)", density)
        self.assertIn("control.classList.toggle('is-collapsed', !initiallyVisible)", density)
        self.assertIn("control.inert = !initiallyVisible", density)
        self.assertIn("setDensityVisible(covers)", surface)
        self.assertIn("densityControl.classList.toggle('is-collapsed', !visible)", surface)
        self.assertIn("densityControl.inert = !visible", surface)
        self.assertIn("max-width 180ms", styles)
        self.assertIn(".cover-size-control.library-density-ready.is-collapsed", styles)
        self.assertIn("max-width: 0;", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)


if __name__ == "__main__":
    unittest.main()
