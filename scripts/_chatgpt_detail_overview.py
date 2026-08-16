from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_template() -> None:
    path = ROOT / "app/templates/detail.html"
    text = path.read_text(encoding="utf-8")
    old = '''  <aside class="title-hero-aside">\n    {% if title.overview %}<section class="title-synopsis"><h2>Synopsis</h2><p class="title-overview">{{ title.overview }}</p></section>{% endif %}\n    {% if actors or (title.kind == 'movie' and (directors or writers)) %}<div class="movie-credits" aria-label="IMDb title credits">\n'''
    new = '''  <aside class="title-hero-aside">\n    <section class="title-synopsis">\n      <h2>Overview</h2>\n      {% if title.overview %}<p class="title-overview">{{ title.overview }}</p>\n      {% else %}<div class="title-overview-empty"><p>No synopsis is cached for this title yet.</p>{% if current_user and current_user.is_librarian and tvdb_enabled %}<form method="post" action="/titles/{{ title.id }}/metadata/enrich"><button type="submit" class="overview-refresh">Refresh metadata</button></form>{% endif %}</div>{% endif %}\n    </section>\n    {% if actors or (title.kind == 'movie' and (directors or writers)) %}<section class="movie-credits" aria-label="IMDb title credits"><h2>Cast &amp; crew</h2>\n'''
    text = replace_once(text, old, new, "hero overview section")
    text = replace_once(text, "</div>{% endif %}\n  </aside>", "</section>{% endif %}\n  </aside>", "credits section close")
    path.write_text(text, encoding="utf-8")


def patch_css() -> None:
    path = ROOT / "app/static/library.css"
    text = path.read_text(encoding="utf-8")
    old = '''.detail-page-head {\n  display: grid;\n  grid-template-columns: 240px minmax(590px, 700px) minmax(440px, 1fr);\n  gap: clamp(34px, 2.6vw, 48px);\n  align-items: start;\n  margin-bottom: 30px;\n}\n'''
    new = '''.detail-page-head {\n  display: grid;\n  grid-template-columns: 240px minmax(430px, .9fr) minmax(360px, 1.1fr);\n  gap: clamp(32px, 2.5vw, 46px);\n  align-items: start;\n  margin-bottom: 30px;\n}\n'''
    text = replace_once(text, old, new, "detail grid")
    old = '''.title-hero-aside { display: grid; align-content: start; gap: 24px; min-width: 0; padding-top:7px; }\n.title-synopsis,.title-at-a-glance { display:grid; gap:9px; }\n.title-synopsis h2,.title-at-a-glance h2 { margin:0; color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }\n.title-overview { max-width: 64ch; margin: 0; color: #b2bdc8; font-size: 16px; line-height: 1.7; }\n'''
    new = '''.title-hero-aside {\n  display:grid;\n  align-content:start;\n  gap:22px;\n  min-width:0;\n  min-height:340px;\n  padding:8px 0 0 clamp(24px,2vw,34px);\n  border-left:1px solid var(--line);\n}\n.title-synopsis,.title-at-a-glance { display:grid; gap:10px; }\n.title-synopsis h2,.title-at-a-glance h2,.movie-credits h2 { margin:0; color:var(--muted); font-size:11px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }\n.title-overview {\n  display:-webkit-box;\n  max-width:64ch;\n  margin:0;\n  overflow:hidden;\n  color:#b9c4cf;\n  font-size:16px;\n  line-height:1.68;\n  -webkit-box-orient:vertical;\n  -webkit-line-clamp:6;\n}\n.title-overview-empty { display:grid; gap:10px; max-width:56ch; }\n.title-overview-empty p { margin:0; color:var(--muted); line-height:1.55; }\n.title-overview-empty form { margin:0; }\n.overview-refresh { padding:6px 9px; background:transparent; color:var(--cyan); font-size:11px; }\n'''
    text = replace_once(text, old, new, "hero aside styles")
    old = '''.movie-credits {\n  position: static;\n  display: grid;\n  gap: 7px;\n  max-width: 570px;\n  margin: 0;\n  color: var(--muted);\n  font-size: 15px;\n}\n\n.movie-credits > div {\n  display: grid;\n  grid-template-columns: 92px 1fr;\n  gap: 10px;\n}\n'''
    new = '''.movie-credits {\n  position:static;\n  display:grid;\n  gap:9px;\n  max-width:570px;\n  margin:0;\n  padding-top:18px;\n  border-top:1px solid var(--line);\n  color:var(--muted);\n  font-size:14px;\n}\n\n.movie-credits > div {\n  display:grid;\n  grid-template-columns:82px minmax(0,1fr);\n  gap:10px;\n}\n'''
    text = replace_once(text, old, new, "credits styles")
    marker = '''@media (max-width:760px) { .bulk-portals { grid-template-columns:1fr; } }\n'''
    responsive = '''@media (max-width:1180px) {\n  .detail-page-head { grid-template-columns:220px minmax(360px,1fr); }\n  .detail-poster-column,.detail-poster,.detail-poster-placeholder { width:220px; }\n  .detail-poster,.detail-poster-placeholder { height:330px; }\n  .title-hero-aside { grid-column:2; min-height:0; margin-top:-108px; }\n}\n@media (max-width:820px) {\n  .detail-page-head { grid-template-columns:1fr; }\n  .detail-poster-column { width:auto; }\n  .detail-poster,.detail-poster-placeholder { width:180px; height:270px; }\n  .title-hero-aside { grid-column:auto; margin-top:0; padding:20px 0 0; border-top:1px solid var(--line); border-left:0; }\n}\n'''
    if responsive not in text:
        text = replace_once(text, marker, marker + responsive, "detail responsive styles")
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = ROOT / "tests/test_detail_overview.py"
    path.write_text('''from pathlib import Path\nimport unittest\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\nclass DetailOverviewTests(unittest.TestCase):\n    def test_detail_hero_always_has_overview_region_and_explicit_refresh(self):\n        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")\n        self.assertIn('<h2>Overview</h2>', template)\n        self.assertIn('No synopsis is cached for this title yet.', template)\n        self.assertIn('/metadata/enrich', template)\n        self.assertIn('Cast &amp; crew', template)\n\n    def test_detail_get_remains_provider_read_only(self):\n        routes = (ROOT / "app/routes/titles.py").read_text(encoding="utf-8")\n        detail_start = routes.index('@router.get("/titles/{title_id}", response_class=HTMLResponse)')\n        cover_start = routes.index('@librarian_get("/titles/{title_id}/cover"', detail_start)\n        detail_route = routes[detail_start:cover_start]\n        self.assertNotIn('TitleMetadataService(', detail_route)\n        self.assertNotIn('tvdb.movie(', detail_route)\n        self.assertNotIn('tvdb.series(', detail_route)\n\n    def test_hero_uses_dedicated_overview_column(self):\n        css = (ROOT / "app/static/library.css").read_text(encoding="utf-8")\n        self.assertIn('minmax(360px, 1.1fr)', css)\n        self.assertIn('border-left:1px solid var(--line)', css)\n        self.assertIn('-webkit-line-clamp:6', css)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")


def main() -> None:
    patch_template()
    patch_css()
    patch_tests()


if __name__ == "__main__":
    main()
