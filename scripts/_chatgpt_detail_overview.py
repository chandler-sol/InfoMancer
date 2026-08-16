from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_titles() -> None:
    path = ROOT / "app/routes/titles.py"
    text = path.read_text(encoding="utf-8")
    anchor = '''    @router.get("/titles/{title_id}", response_class=HTMLResponse)\n    def title_detail(request: Request, title_id: int):\n        with db.connect() as conn:\n'''
    replacement = '''    def maybe_backfill_detail_overview(title_id: int) -> None:\n        \"\"\"One-time provider backfill for legacy matched titles missing an overview.\"\"\"\n        with db.connect() as conn:\n            row = conn.execute(\n                \"\"\"SELECT id, kind, overview, tvdb_id, tvdb_movie_id, tmdb_id, imdb_id,\n                          metadata_refreshed_at\n                   FROM titles WHERE id=?\"\"\",\n                (title_id,),\n            ).fetchone()\n        if not row or row[\"overview\"] or row[\"metadata_refreshed_at\"]:\n            return\n        if row[\"kind\"] == \"tv\":\n            provider_identity = bool(row[\"tvdb_id\"])\n        else:\n            provider_identity = bool(\n                row[\"tvdb_movie_id\"] or row[\"tmdb_id\"] or row[\"imdb_id\"]\n            )\n        if not provider_identity:\n            return\n        service = TitleMetadataService(\n            db, tvdb, poster_from=poster_from, plex_movie_ids=plex_movie_ids,\n            localized_title=localized_tvdb_title, match_confidence=match_confidence,\n        )\n        error = \"\"\n        changed = False\n        try:\n            changed = service.enrich(title_id)\n        except (TVDBError, OSError, ValueError) as exc:\n            error = str(exc)[:500]\n        with db.connect() as conn:\n            conn.execute(\n                \"\"\"UPDATE titles SET metadata_refreshed_at=COALESCE(metadata_refreshed_at, CURRENT_TIMESTAMP),\n                   metadata_refresh_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?\"\"\",\n                (error if error else (\"\" if changed else \"No provider synopsis was available.\"), title_id),\n            )\n        if error:\n            record_event(\n                \"metadata\", \"Title overview backfill could not finish.\",\n                level=\"warning\", detail=error, context={\"title_id\": title_id},\n            )\n        elif changed:\n            record_event(\n                \"metadata\", \"Title overview was backfilled for the detail view.\",\n                context={\"title_id\": title_id},\n            )\n\n    @router.get("/titles/{title_id}", response_class=HTMLResponse)\n    def title_detail(request: Request, title_id: int):\n        maybe_backfill_detail_overview(title_id)\n        with db.connect() as conn:\n'''
    text = replace_once(text, anchor, replacement, "title detail backfill")
    path.write_text(text, encoding="utf-8")


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
    # Insert responsive behavior near the existing mobile section marker if present.
    marker = '''@media (max-width:760px) { .bulk-portals { grid-template-columns:1fr; } }\n'''
    responsive = '''@media (max-width:1180px) {\n  .detail-page-head { grid-template-columns:220px minmax(360px,1fr); }\n  .detail-poster-column,.detail-poster,.detail-poster-placeholder { width:220px; }\n  .detail-poster,.detail-poster-placeholder { height:330px; }\n  .title-hero-aside { grid-column:2; min-height:0; margin-top:-108px; }\n}\n@media (max-width:820px) {\n  .detail-page-head { grid-template-columns:1fr; }\n  .detail-poster-column { width:auto; }\n  .detail-poster,.detail-poster-placeholder { width:180px; height:270px; }\n  .title-hero-aside { grid-column:auto; margin-top:0; padding:20px 0 0; border-top:1px solid var(--line); border-left:0; }\n}\n'''
    if responsive not in text:
        text = replace_once(text, marker, marker + responsive, "detail responsive styles")
    path.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    path = ROOT / "tests/test_detail_overview.py"
    path.write_text('''from pathlib import Path\nimport unittest\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\nclass DetailOverviewTests(unittest.TestCase):\n    def test_detail_hero_always_has_overview_region(self):\n        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")\n        self.assertIn('<h2>Overview</h2>', template)\n        self.assertIn('No synopsis is cached for this title yet.', template)\n        self.assertIn('/metadata/enrich', template)\n        self.assertIn('Cast &amp; crew', template)\n\n    def test_legacy_matched_titles_get_one_time_overview_backfill(self):\n        routes = (ROOT / "app/routes/titles.py").read_text(encoding="utf-8")\n        self.assertIn('def maybe_backfill_detail_overview', routes)\n        self.assertIn('metadata_refreshed_at', routes)\n        self.assertIn('service.enrich(title_id)', routes)\n        self.assertIn('maybe_backfill_detail_overview(title_id)', routes)\n\n    def test_hero_uses_dedicated_overview_column(self):\n        css = (ROOT / "app/static/library.css").read_text(encoding="utf-8")\n        self.assertIn('minmax(360px, 1.1fr)', css)\n        self.assertIn('border-left:1px solid var(--line)', css)\n        self.assertIn('-webkit-line-clamp:6', css)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")


def main() -> None:
    patch_titles()
    patch_template()
    patch_css()
    patch_tests()


if __name__ == "__main__":
    main()
