import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CollapsibleSeasonContractTests(unittest.TestCase):
    def test_full_tv_detail_starts_seasons_collapsed_with_bulk_controls(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        self.assertIn('class="season-heading"', template)
        self.assertIn('aria-expanded="false"', template)
        self.assertIn('id="expand-all-seasons"', template)
        self.assertIn('id="collapse-all-seasons"', template)
        self.assertIn('const defaultSeasonDisplay = {{ default_season_display|tojson }};', template)
        self.assertIn('defaultSeasonDisplay === "expanded"', template)
        self.assertIn('expandedSeasons.clear();', template)
        self.assertIn('heading.setAttribute("aria-expanded", String(expanded));', template)

    def test_direct_season_filter_expands_selected_season(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        self.assertIn('if (season !== "all") expandedSeasons.add(season);', template)
        self.assertIn('row.hidden = !(inFilter && expanded);', template)
        self.assertIn('Specials{% else %}Season', template)

    def test_windows_packaging_requires_zero_residue_uninstall_and_recovery_offer(self):
        packaging = (ROOT / "docs/PACKAGING.md").read_text(encoding="utf-8")
        self.assertIn("Windows uninstall contract", packaging)
        self.assertIn("Create recovery backup & uninstall", packaging)
        self.assertIn("explicit ownership", packaging)
        self.assertIn("Media files and user-selected recovery packages are never deleted", packaging)


if __name__ == "__main__":
    unittest.main()
