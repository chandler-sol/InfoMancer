from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Beta2ReleaseCandidatePolishContracts(unittest.TestCase):
    def test_sources_step_explicitly_supports_skip_for_now(self):
        template = (ROOT / "app/templates/getting_started.html").read_text(encoding="utf-8")
        self.assertIn('href="/getting-started/finish">Skip for now</a>', template)
        self.assertIn("skip for now and add your sources later from Settings", template)
        self.assertIn('disabled aria-disabled="true"', template)
        self.assertNotIn("Add at least one Movie or TV Shows folder to continue.", template)

    def test_finish_copy_handles_no_sources_without_promising_scan(self):
        template = (ROOT / "app/templates/getting_started.html").read_text(encoding="utf-8")
        self.assertIn("{% if roots %}", template)
        self.assertIn("You're ready to finish setup", template)
        self.assertIn("No media folders are connected yet", template)
        self.assertIn("roots|length if roots else 'Not added yet'", template)

    def test_about_page_is_compact_and_credits_creator(self):
        template = (ROOT / "app/templates/about.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/engagement.css").read_text(encoding="utf-8")
        self.assertIn("Thank you to the third-party services", template)
        self.assertIn("Created by Chandler Solomon", template)
        self.assertIn("https://infomancer.media/", template)
        self.assertNotIn("<h2>Independent software</h2>", template)
        self.assertIn(".about-heading{position:relative;max-width:1120px", css)
        self.assertIn(".provider-wordmark{min-height:76px", css)
        self.assertIn(".about-project-credit", css)

    def test_linux_package_exposes_software_center_identity(self):
        metainfo = (ROOT / "desktop/src-tauri/linux/cloud.arsenik.infomancer.metainfo.xml").read_text(encoding="utf-8")
        desktop = (ROOT / "desktop/src-tauri/linux/InfoMancer.desktop.hbs").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/draft-08-release.yml").read_text(encoding="utf-8")
        self.assertIn('<name>InfoMancer</name>', metainfo)
        self.assertIn('<icon type="stock">infomancer-desktop</icon>', metainfo)
        self.assertIn('<release version="0.8.1-beta.2" date="2026-09-10">', metainfo)
        self.assertIn('Name={{name}}', desktop)
        self.assertIn('Icon={{icon}}', desktop)
        self.assertIn("grep -q '^Name=InfoMancer$'", workflow)
        self.assertIn("grep -q '^Icon=infomancer-desktop$'", workflow)


if __name__ == "__main__":
    unittest.main()
