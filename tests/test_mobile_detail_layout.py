from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MobileDetailLayoutTests(unittest.TestCase):
    def test_mobile_title_overview_flattens_nested_dossier_cards(self):
        css = (ROOT / "app/static/mobile-detail.css").read_text(encoding="utf-8")
        bootstrap = (ROOT / "app/static/app-shell-bootstrap.js").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 620px)", css)
        self.assertIn(".media-dossier .title-hero-aside", css)
        self.assertIn(".media-dossier .title-synopsis", css)
        self.assertIn(".media-dossier .movie-credits", css)
        self.assertIn("border-radius: 0", css)
        self.assertIn("background: transparent", css)
        self.assertIn("border-top: 1px solid var(--line)", css)
        self.assertIn("/static/mobile-detail.css", bootstrap)


if __name__ == "__main__":
    unittest.main()
