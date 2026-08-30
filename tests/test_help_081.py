from pathlib import Path
import unittest


class Help081ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.help_html = (root / "app/templates/help.html").read_text(encoding="utf-8")

    def test_help_covers_current_desktop_and_network_source_behavior(self):
        self.assertIn("Standalone Desktop", self.help_html)
        self.assertIn("connect to an existing InfoMancer server", self.help_html)
        self.assertIn("mapped drive letters", self.help_html)
        self.assertIn("WinError 1272", self.help_html)
        self.assertIn("NFS", self.help_html)
        self.assertIn("SMB", self.help_html)

    def test_help_preserves_review_first_matching_and_file_safety_contracts(self):
        self.assertIn("Automatic movie and TV suggestions still require review", self.help_html)
        self.assertIn("Unresolved work remains available for later review", self.help_html)
        self.assertIn("<strong>Standard</strong>", self.help_html)
        self.assertIn("<strong>Read-Only</strong>", self.help_html)
        self.assertIn("<strong>Lockdown</strong>", self.help_html)
        self.assertIn("revalidates important filesystem assumptions", self.help_html)

    def test_help_explains_media_inspection_and_recovery_boundaries(self):
        self.assertIn("Supported native desktop builds include a verified FFprobe binary", self.help_html)
        self.assertIn("A failure does not automatically mean FFprobe is broken", self.help_html)
        self.assertIn(".infomancer-backup", self.help_html)
        self.assertIn("Provider credentials and encryption secrets are intentionally excluded", self.help_html)
        self.assertIn("does not contain your media files", self.help_html)

    def test_help_navigation_links_every_major_section(self):
        for section in (
            "getting-started",
            "installation",
            "libraries",
            "matching",
            "inspection",
            "episodes",
            "files",
            "organization",
            "accounts",
            "backups",
            "troubleshooting",
        ):
            self.assertIn(f'href="#{section}"', self.help_html)
            self.assertIn(f'id="{section}"', self.help_html)


if __name__ == "__main__":
    unittest.main()
