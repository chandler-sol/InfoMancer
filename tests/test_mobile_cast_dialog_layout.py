from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MobileCastDialogLayoutTests(unittest.TestCase):
    def test_close_button_is_anchored_out_of_heading_flow(self):
        css = (ROOT / "app/static/title-cast-dialog.css").read_text(encoding="utf-8")

        head = css.split(".title-cast-dialog-head {", 1)[1].split("}", 1)[0]
        close = css.split(".title-cast-dialog-close {", 1)[1].split("}", 1)[0]

        self.assertIn("position: relative", head)
        self.assertIn("padding: 20px 70px 16px 22px", head)
        self.assertIn("position: absolute", close)
        self.assertIn("top: 16px", close)
        self.assertIn("right: 18px", close)

    def test_mobile_close_target_stays_top_right_and_touch_sized(self):
        css = (ROOT / "app/static/title-cast-dialog.css").read_text(encoding="utf-8")
        mobile = css.split("@media (max-width: 560px)", 1)[1]

        self.assertIn(".title-cast-dialog-close", mobile)
        self.assertIn("top: 12px", mobile)
        self.assertIn("right: 12px", mobile)
        self.assertIn("width: 44px", mobile)
        self.assertIn("height: 44px", mobile)


if __name__ == "__main__":
    unittest.main()
