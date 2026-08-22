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


if __name__ == "__main__":
    unittest.main()
