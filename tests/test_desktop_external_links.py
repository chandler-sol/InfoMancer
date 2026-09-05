from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DesktopExternalLinkTests(unittest.TestCase):
    def test_main_window_is_built_with_native_new_window_handler(self):
        config = (ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        main = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
        external = (ROOT / "desktop/src-tauri/src/external_links.rs").read_text(encoding="utf-8")

        self.assertIn('"windows": []', config)
        self.assertIn("mod external_links;", main)
        self.assertIn(".setup(external_links::setup)", main)
        self.assertIn('WebviewWindowBuilder::new(app, "main"', external)
        self.assertIn(".on_new_window", external)
        self.assertIn("NewWindowResponse::Deny", external)

    def test_external_browser_boundary_only_accepts_safe_http_urls(self):
        external = (ROOT / "desktop/src-tauri/src/external_links.rs").read_text(encoding="utf-8")
        template = (ROOT / "app/templates/tvdb.html").read_text(encoding="utf-8")

        self.assertIn('matches!(url.scheme(), "http" | "https")', external)
        self.assertIn("url.username().is_empty()", external)
        self.assertIn("url.password().is_none()", external)
        self.assertIn("url.dll,FileProtocolHandler", external)
        self.assertIn('Command::new("open")', external)
        self.assertIn('Command::new("xdg-open")', external)
        self.assertIn('target="_blank"', template)
        self.assertIn("Search TVDB website", template)


if __name__ == "__main__":
    unittest.main()
