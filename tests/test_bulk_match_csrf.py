import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BulkMatchCsrfTemplateTests(unittest.TestCase):
    def test_every_bulk_match_post_form_includes_csrf_token(self):
        for relative_path in (
            "app/templates/bulk_movie_match.html",
            "app/templates/bulk_tv_match.html",
        ):
            with self.subTest(template=relative_path):
                template = (ROOT / relative_path).read_text(encoding="utf-8")
                forms = re.findall(
                    r'<form\b[^>]*method="post"[^>]*>(.*?)</form>',
                    template,
                    flags=re.DOTALL,
                )
                self.assertTrue(forms, "Expected at least one POST form")
                for index, form_body in enumerate(forms, start=1):
                    self.assertIn(
                        'name="csrf_token" value="{{ csrf_token }}"',
                        form_body,
                        f"POST form {index} is missing its CSRF token",
                    )


if __name__ == "__main__":
    unittest.main()
