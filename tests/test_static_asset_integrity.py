from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
STATIC = APP / "static"
TEMPLATES = APP / "templates"


STATIC_URL_RE = re.compile(
    r"/static/([A-Za-z0-9_./-]+\.(?:css|json|js|svg|png|webp|ico))(?![A-Za-z0-9])"
)
LOADER_RE = re.compile(
    r"(?:loadStyle|loadScript|ensureStyle)\(\s*['\"]([^'\"]+\.(?:css|js))['\"]"
)
TEMPLATE_STATIC_RE = re.compile(
    r"url_for\(\s*['\"]static['\"]\s*,\s*path\s*=\s*['\"]([^'\"]+)['\"]"
)


class StaticAssetIntegrityTests(unittest.TestCase):
    def _assert_assets_exist(self, names: set[str], owner: str) -> None:
        missing = sorted(name for name in names if not (STATIC / name).is_file())
        self.assertEqual(missing, [], f"{owner} references missing static assets: {missing}")

    def test_javascript_runtime_asset_references_exist(self) -> None:
        names: set[str] = set()
        for path in STATIC.glob("*.js"):
            text = path.read_text(encoding="utf-8")
            names.update(STATIC_URL_RE.findall(text))
            names.update(LOADER_RE.findall(text))
        self._assert_assets_exist(names, "JavaScript runtime")

    def test_template_static_asset_references_exist(self) -> None:
        names: set[str] = set()
        for path in TEMPLATES.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            names.update(TEMPLATE_STATIC_RE.findall(text))
            names.update(STATIC_URL_RE.findall(text))
        self._assert_assets_exist(names, "Templates")

    def test_shell_bootstrap_assets_are_versioned(self) -> None:
        bootstrap = (STATIC / "app-shell-bootstrap.js").read_text(encoding="utf-8")
        names = set(STATIC_URL_RE.findall(bootstrap))
        self.assertTrue(names)
        for name in names:
            self.assertIn(
                f"/static/{name}${{versionQuery}}",
                bootstrap,
                f"Bootstrap asset {name} bypasses the static version query",
            )


if __name__ == "__main__":
    unittest.main()
