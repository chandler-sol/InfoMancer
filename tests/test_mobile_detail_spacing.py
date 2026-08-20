import unittest
from pathlib import Path


class MobileDetailSpacingTests(unittest.TestCase):
    def test_mobile_title_detail_tightens_shell_below_header(self):
        css = Path("app/static/detail-page-polish.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("body.has-app-sidebar main.shell:has(> .media-dossier)", css)
        self.assertIn("padding-top: 18px;", css)


if __name__ == "__main__":
    unittest.main()
