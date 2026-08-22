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

    def test_phone_hero_keeps_poster_and_identity_in_one_row(self):
        css = (ROOT / "app/static/mobile-detail.css").read_text(encoding="utf-8")

        self.assertIn(
            "grid-template-columns: clamp(108px, 31vw, 138px) minmax(0, 1fr)",
            css,
        )
        self.assertIn(".detail-page-head .detail-poster-column", css)
        self.assertIn("aspect-ratio: 2 / 3", css)
        self.assertIn(".detail-page-head .detail-copy", css)
        self.assertIn("grid-column: 2", css)
        self.assertIn("grid-column: 1 / -1", css)
        self.assertNotIn("grid-template-columns: 1fr;", css)

    def test_mobile_on_disk_header_uses_file_content_axes(self):
        css = (ROOT / "app/static/mobile-detail.css").read_text(encoding="utf-8")

        self.assertIn(".dossier-on-disk > .panel-head", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", css)
        self.assertIn(".dossier-on-disk .on-disk-copy", css)
        self.assertIn("justify-self: start", css)
        self.assertIn(".dossier-on-disk .dossier-file-count", css)
        self.assertIn("justify-self: end", css)

    def test_mobile_detail_styles_load_after_general_mobile_polish(self):
        bootstrap = (ROOT / "app/static/app-shell-bootstrap.js").read_text(encoding="utf-8")

        self.assertLess(
            bootstrap.index("/static/final-mobile-polish.css"),
            bootstrap.index("/static/mobile-detail.css"),
        )


if __name__ == "__main__":
    unittest.main()
