from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return text.replace(old, new, 1)


detail_path = ROOT / "app/templates/detail.html"
detail = detail_path.read_text(encoding="utf-8")

overview_markup = '<p class="title-overview">{{ title.overview }}</p>'
overview_replacement = '<p class="title-overview" id="title-overview">{{ title.overview }}</p><button type="button" class="overview-more" id="overview-more" hidden aria-haspopup="dialog" aria-controls="overview-dialog">See full overview</button><dialog id="overview-dialog" class="workspace-confirm-dialog title-overview-dialog" aria-labelledby="overview-dialog-title"><section class="workspace-dialog-card"><p class="eyebrow">FULL OVERVIEW</p><div class="overview-dialog-heading"><h2 id="overview-dialog-title">{{ title.metadata_title or title.title }}</h2><button type="button" class="overview-dialog-close" data-overview-close aria-label="Close full overview">×</button></div><p class="overview-dialog-copy">{{ title.overview }}</p><div class="workspace-dialog-actions"><button type="button" class="button" data-overview-close>Close</button></div></section></dialog>'
detail = replace_once(detail, overview_markup, overview_replacement, "overview markup")

script_anchor = '  // The episode action already carries the canonical TVDB URL.'
overview_js = '''  const overview = document.getElementById("title-overview");
  const overviewMore = document.getElementById("overview-more");
  const overviewDialog = document.getElementById("overview-dialog");
  const syncOverviewMore = () => {
    if (!overview || !overviewMore) return;
    overviewMore.hidden = !(overview.scrollHeight > overview.clientHeight + 1);
  };
  const closeOverviewDialog = () => {
    if (!overviewDialog) return;
    if (typeof overviewDialog.close === "function" && overviewDialog.open) overviewDialog.close();
    else overviewDialog.removeAttribute("open");
  };
  overviewMore?.addEventListener("click", () => {
    if (!overviewDialog) return;
    if (typeof overviewDialog.showModal === "function") overviewDialog.showModal();
    else overviewDialog.setAttribute("open", "");
  });
  overviewDialog?.querySelectorAll("[data-overview-close]").forEach((button) => {
    button.addEventListener("click", closeOverviewDialog);
  });
  overviewDialog?.addEventListener("click", (event) => {
    if (event.target === overviewDialog) closeOverviewDialog();
  });
  window.addEventListener("resize", syncOverviewMore);
  window.addEventListener("load", syncOverviewMore, { once: true });
  window.requestAnimationFrame(syncOverviewMore);

''' + script_anchor
detail = replace_once(detail, script_anchor, overview_js, "overview script")

menu_anchor = '''  document.addEventListener("click", (event) => {
    document.querySelectorAll(".series-menu[open], .episode-menu[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
  });'''
menu_js = '''  const movieMenus = Array.from(document.querySelectorAll(".movie-detail-menu"));
  const fitMovieMenu = (menu) => {
    const popover = menu.querySelector(".series-menu-popover");
    const summary = menu.querySelector(":scope > summary");
    if (!popover || !summary || !menu.open) return;
    menu.classList.remove("menu-open-up");
    popover.style.maxHeight = "none";
    const trigger = summary.getBoundingClientRect();
    const naturalHeight = popover.scrollHeight;
    const roomBelow = Math.max(0, window.innerHeight - trigger.bottom - 12);
    const roomAbove = Math.max(0, trigger.top - 12);
    const openUp = naturalHeight > roomBelow && roomAbove > roomBelow;
    menu.classList.toggle("menu-open-up", openUp);
    const available = openUp ? roomAbove : roomBelow;
    popover.style.maxHeight = `${Math.max(96, Math.floor(available - 8))}px`;
  };
  movieMenus.forEach((menu) => {
    menu.addEventListener("toggle", () => {
      if (!menu.open) {
        menu.classList.remove("menu-open-up");
        menu.querySelector(".series-menu-popover")?.style.removeProperty("max-height");
        return;
      }
      window.requestAnimationFrame(() => fitMovieMenu(menu));
    });
  });
  const refitMovieMenus = () => movieMenus.forEach((menu) => menu.open && fitMovieMenu(menu));
  window.addEventListener("resize", refitMovieMenus);
  window.addEventListener("scroll", refitMovieMenus, true);

''' + menu_anchor
detail = replace_once(detail, menu_anchor, menu_js, "movie menu")
detail_path.write_text(detail, encoding="utf-8")

workspace_path = ROOT / "app/static/workspace.js"
workspace = workspace_path.read_text(encoding="utf-8")
old_close = '''    const scheduleClose = () => {
      window.clearTimeout(closeTimer);
      closeTimer = window.setTimeout(() => {
        popover.hidden = true;
        activeLink = null;
      }, 140);
    };'''
new_close = '''    const closeNow = () => {
      window.clearTimeout(openTimer);
      window.clearTimeout(closeTimer);
      popover.hidden = true;
      activeLink = null;
    };

    const scheduleClose = () => {
      window.clearTimeout(openTimer);
      window.clearTimeout(closeTimer);
      closeTimer = window.setTimeout(closeNow, 120);
    };'''
workspace = replace_once(workspace, old_close, new_close, "person close")
old_end = '''    popover.addEventListener("pointerenter", () => window.clearTimeout(closeTimer));
    popover.addEventListener("pointerleave", scheduleClose);
    window.addEventListener("resize", () => activeLink && position(activeLink));
    window.addEventListener("scroll", () => activeLink && position(activeLink), true);'''
new_end = '''    popover.addEventListener("pointerenter", () => window.clearTimeout(closeTimer));
    popover.addEventListener("pointerleave", scheduleClose);
    document.addEventListener("pointerdown", (event) => {
      if (popover.hidden || popover.contains(event.target) || activeLink?.contains(event.target)) return;
      closeNow();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !popover.hidden) closeNow();
    });
    window.addEventListener("resize", () => activeLink && position(activeLink));
    window.addEventListener("scroll", () => activeLink && position(activeLink), true);'''
workspace = replace_once(workspace, old_end, new_end, "person popover end")
workspace_path.write_text(workspace, encoding="utf-8")

css_path = ROOT / "app/static/workspace-ui.css"
css = css_path.read_text(encoding="utf-8")
marker = "/* Detail viewport/menu and full-overview polish. */"
if marker not in css:
    css += '''

/* Detail viewport/menu and full-overview polish. */
body.has-app-sidebar .media-dossier .overview-more {
  justify-self: start;
  margin: -2px 0 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--cyan);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.4;
  text-decoration: none;
}
body.has-app-sidebar .media-dossier .overview-more:hover,
body.has-app-sidebar .media-dossier .overview-more:focus-visible {
  color: var(--lime);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.title-overview-dialog { width: min(720px, calc(100vw - 32px)); }
.title-overview-dialog .workspace-dialog-card { gap: 14px; }
.title-overview-dialog .overview-dialog-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}
.title-overview-dialog .overview-dialog-heading h2 {
  margin: 0;
  color: var(--text);
  font-size: clamp(24px, 4vw, 34px);
  letter-spacing: -.025em;
  text-transform: none;
}
.title-overview-dialog .overview-dialog-close {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  font-size: 24px;
  line-height: 1;
}
.title-overview-dialog .overview-dialog-close:hover,
.title-overview-dialog .overview-dialog-close:focus-visible {
  background: rgba(255,255,255,.055);
  color: var(--text);
}
.title-overview-dialog .overview-dialog-copy {
  max-width: 66ch;
  margin: 0;
  color: #c3ced8;
  font-size: 16px;
  line-height: 1.72;
  white-space: pre-line;
}
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu > .series-menu-popover {
  top: calc(100% + 7px);
  bottom: auto;
  width: 270px;
  padding: 6px;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu.menu-open-up > .series-menu-popover {
  top: auto;
  bottom: calc(100% + 7px);
}
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu .series-menu-popover > a,
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu .series-menu-popover > span,
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu .series-menu-popover form > button {
  min-height: 31px;
  padding: 5px 8px;
  font-size: 11.5px;
}
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu .series-menu-popover hr { margin: 4px 3px; }
body.has-app-sidebar .media-dossier .dossier-on-disk .movie-detail-menu .series-menu-popover > .menu-section-label {
  min-height: 0;
  padding: 4px 8px 2px;
  font-size: 8.5px;
}
@media (max-width: 700px) {
  .title-overview-dialog { width: calc(100vw - 20px); }
  .title-overview-dialog .workspace-dialog-card { padding: 18px; }
}
'''
css_path.write_text(css, encoding="utf-8")

test_path = ROOT / "tests/test_detail_interaction_polish.py"
test_path.write_text('''from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent

class DetailInteractionPolishTests(unittest.TestCase):
    def test_long_overview_has_accessible_full_text_dialog(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        self.assertIn('id="overview-more"', template)
        self.assertIn('See full overview', template)
        self.assertIn('id="overview-dialog"', template)
        self.assertIn('aria-labelledby="overview-dialog-title"', template)
        self.assertIn('overview.scrollHeight > overview.clientHeight + 1', template)

    def test_movie_action_menu_is_viewport_aware(self):
        template = (ROOT / "app/templates/detail.html").read_text(encoding="utf-8")
        css = (ROOT / "app/static/workspace-ui.css").read_text(encoding="utf-8")
        self.assertIn('const fitMovieMenu = (menu) =>', template)
        self.assertIn('naturalHeight > roomBelow && roomAbove > roomBelow', template)
        self.assertIn('menu-open-up', template)
        self.assertIn('.movie-detail-menu.menu-open-up > .series-menu-popover', css)
        self.assertIn('overflow-y: auto', css)

    def test_person_hover_preview_cancels_pending_open_on_leave(self):
        script = (ROOT / "app/static/workspace.js").read_text(encoding="utf-8")
        schedule = script.split('const scheduleClose = () => {', 1)[1].split('};', 1)[0]
        self.assertIn('window.clearTimeout(openTimer);', schedule)
        self.assertIn('window.setTimeout(closeNow, 120)', schedule)
        self.assertIn('event.key === "Escape" && !popover.hidden', script)

if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")
