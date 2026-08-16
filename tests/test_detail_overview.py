from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DetailOverviewTests(unittest.TestCase):
    def test_detail_hero_always_has_overview_region_and_explicit_refresh(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        self.assertIn('<h2>Overview</h2>', template)
        self.assertIn('No synopsis is cached for this title yet.', template)
        self.assertIn('/metadata/enrich', template)
        self.assertIn('Cast &amp; crew', template)

    def test_detail_get_remains_provider_read_only(self):
        routes = (ROOT / "app/routes/titles.py").read_text(encoding="utf-8")
        detail_start = routes.index('@router.get("/titles/{title_id}", response_class=HTMLResponse)')
        cover_start = routes.index('@librarian_get("/titles/{title_id}/cover"', detail_start)
        detail_route = routes[detail_start:cover_start]
        self.assertNotIn('TitleMetadataService(', detail_route)
        self.assertNotIn('tvdb.movie(', detail_route)
        self.assertNotIn('tvdb.series(', detail_route)

    def test_hero_uses_dedicated_overview_column(self):
        css = (ROOT / "app/static/library.css").read_text(encoding="utf-8")
        self.assertIn('minmax(360px, 1.1fr)', css)
        self.assertIn('border-left:1px solid var(--line)', css)
        self.assertIn('-webkit-line-clamp:6', css)


if __name__ == "__main__":
    unittest.main()
