from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.routes.source_commit import _validated_source_path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
ROUTES = ROOT / "app" / "routes"


class SourceCommitRegressionTests(unittest.TestCase):
    def test_submitted_source_uses_browser_validation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            child = root / "Movies"
            child.mkdir()
            self.assertEqual(
                _validated_source_path(str(child), (root,)),
                str(child.resolve()),
            )

    def test_safe_source_commit_route_precedes_legacy_settings_route(self) -> None:
        init = (ROUTES / "__init__.py").read_text(encoding="utf-8")
        safe_index = init.index("build_source_commit_router,")
        settings_index = init.index("build_settings_router,")
        self.assertLess(safe_index, settings_index)

        source_commit = (ROUTES / "source_commit.py").read_text(encoding="utf-8")
        self.assertIn('@router.post("/roots"', source_commit)
        self.assertIn("validate_browse_path", source_commit)
        self.assertIn("allowed_roots", source_commit)
        self.assertNotIn(".resolve()", source_commit.split("def add_root_safe", 1)[1])

    def test_source_browser_x_has_final_transform_authority(self) -> None:
        dialog_css = (STATIC / "dialog-controls.css").read_text(encoding="utf-8")
        modern_css = (STATIC / "modern.css").read_text(encoding="utf-8")

        # modern.css still carries an older generic dialog rule. The canonical
        # renderer must explicitly beat its higher-specificity transform until
        # that broader stylesheet is cleaned up.
        self.assertIn('dialog button[aria-label^="Close"]::before', modern_css)
        self.assertIn("dialog button.source-browser-close::before", dialog_css)
        self.assertIn(
            "transform: translate(-50%, -50%) rotate(45deg) !important",
            dialog_css,
        )
        self.assertIn(
            "transform: translate(-50%, -50%) rotate(-45deg) !important",
            dialog_css,
        )


if __name__ == "__main__":
    unittest.main()
