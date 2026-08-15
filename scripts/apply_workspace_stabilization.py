from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Expected {label} block was not found")
    return text.replace(old, new, 1)


base_path = ROOT / "app" / "templates" / "base.html"
base = base_path.read_text(encoding="utf-8")

brand_old = '''    <a class="brand" href="/" aria-label="InfoMancer Home">
      <img class="brand-lockup" src="{{ url_for('static', path='infomancer-lockup.svg') }}" alt="InfoMancer">
      <img class="brand-icon" src="{{ url_for('static', path='infomancer-icon.svg') }}" alt="">
    </a>'''
brand_new = '''    <a class="brand" href="/" aria-label="InfoMancer Home">
      <img class="brand-lockup" src="{{ url_for('static', path='infomancer-lockup.svg') }}" alt="InfoMancer">
      <img class="brand-icon" src="{{ url_for('static', path='infomancer-icon.svg') }}" alt="">
      <span class="workspace-nav-alpha" title="InfoMancer 0.8 Alpha Workspace" aria-label="Version 0.8 Alpha Workspace">0.8 α</span>
    </a>'''
base = replace_once(base, brand_old, brand_new, "brand")

home_start_marker = '''      {% if request.url.path == '/' and current_user.id > 0 %}
      <form class="home-layout-toggle"'''
home_start = base.find(home_start_marker)
if home_start < 0:
    raise RuntimeError("Expected home-layout switcher was not found")
home_end_marker = "      {% endif %}\n"
home_end = base.find(home_end_marker, home_start)
if home_end < 0:
    raise RuntimeError("Could not locate end of home-layout switcher")
home_end += len(home_end_marker)
base = base[:home_start] + base[home_end:]

nav_start_marker = '        <nav class="site-menu-panel" id="site-menu-panel" aria-hidden="true">'
nav_start = base.find(nav_start_marker)
if nav_start < 0:
    raise RuntimeError("Expected legacy site navigation was not found")
nav_end_marker = "        </nav>"
nav_end = base.find(nav_end_marker, nav_start)
if nav_end < 0:
    raise RuntimeError("Could not locate end of legacy site navigation")
nav_end += len(nav_end_marker)

workspace_nav = '''        {% set workspace_review_active = request.url.path.startswith('/library-health') or request.url.path.startswith('/duplicates') or request.url.path.startswith('/bulk-match') or request.url.path.startswith('/movies/bulk-match') or request.url.path.startswith('/shows/bulk-match') %}
        {% set workspace_library_active = not workspace_review_active and (request.url.path.startswith('/library') or request.url.path.startswith('/movies') or request.url.path.startswith('/shows') or request.url.path.startswith('/titles') or request.url.path.startswith('/files') or request.url.path.startswith('/collections') or request.url.path.startswith('/libraries') or request.url.path.startswith('/favorites')) %}
        {% set workspace_sources_active = request.url.path.startswith('/sources') %}
        {% set workspace_activity_active = request.url.path.startswith('/activity') or request.url.path.startswith('/announcements') %}
        {% set workspace_more_active = request.url.path.startswith('/settings') or request.url.path.startswith('/help') or request.url.path.startswith('/about') %}
        <nav class="site-menu-panel workspace-nav-ready" id="site-menu-panel" aria-hidden="true" data-workspace-nav>
          <div class="workspace-nav-primary">
            <a href="/" title="Dashboard"{% if request.url.path == '/' %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 11 9-8 9 8"></path><path d="M5.5 9.5V21h13V9.5M9 21v-7h6v7"></path></svg><span>Dashboard</span></a>
            <a href="/library" title="Library"{% if workspace_library_active %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M7 5 9 9m4-4 2 4m4-4 2 4M3 10h18"></path></svg><span>Library</span></a>
            <a href="/library-health" title="Review"{% if workspace_review_active %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-6 4 12 2-6h6"></path><path d="M20 5.8A8.5 8.5 0 0 0 12 3a8.5 8.5 0 0 0-8 2.8"></path><path d="M4.5 17.5A18 18 0 0 0 12 22a18 18 0 0 0 7.5-4.5"></path></svg><span>Review</span></a>
            {% if current_user.is_librarian %}<a href="/sources" title="Sources"{% if workspace_sources_active %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"></path></svg><span>Sources</span></a>{% endif %}
            <a href="/activity" title="Activity"{% if workspace_activity_active %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z"></path><path d="M8 9h8M8 13h5"></path></svg><span>Activity</span>{% if activity_unread_count %}<b class="menu-count">{{ activity_unread_count }}</b>{% endif %}</a>
          </div>

          <details class="workspace-nav-section" data-workspace-section="library"{% if workspace_library_active %} open{% endif %}>
            <summary aria-label="Library shortcuts">Library</summary>
            <div class="workspace-nav-secondary">
              <a href="/movies" title="Movies"{% if request.url.path == '/movies' %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"></rect><path d="M7 5 9 9m4-4 2 4m4-4 2 4M3 10h18"></path></svg><span>Movies</span></a>
              <a href="/shows" title="TV Shows"{% if request.url.path.startswith('/shows') and not request.url.path.startswith('/shows/bulk-match') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="18" height="14" rx="2"></rect><path d="m8 2 4 4 4-4M8 15h8"></path></svg><span>TV Shows</span></a>
              <a href="/collections" title="Collections"{% if request.url.path.startswith('/collections') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="13" height="15" rx="1"></rect><path d="M8 2h12v15M7 9h7M7 13h7"></path></svg><span>Collections</span></a>
              <a href="/libraries" title="Libraries"{% if request.url.path.startswith('/libraries') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M8 4v16M13 8h5M13 12h5"></path></svg><span>Libraries</span></a>
              <a href="/favorites" title="Favorites"{% if request.url.path.startswith('/favorites') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2.5 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3.1-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9z"></path></svg><span>Favorites</span></a>
            </div>
          </details>

          <details class="workspace-nav-section" data-workspace-section="review"{% if workspace_review_active %} open{% endif %}>
            <summary aria-label="Review shortcuts">Review</summary>
            <div class="workspace-nav-secondary">
              <a href="/library-health" title="Library Health"{% if request.url.path.startswith('/library-health') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-6 4 12 2-6h6"></path><path d="M20 5.8A8.5 8.5 0 0 0 12 3a8.5 8.5 0 0 0-8 2.8"></path><path d="M4.5 17.5A18 18 0 0 0 12 22a18 18 0 0 0 7.5-4.5"></path></svg><span>Library Health</span></a>
              {% if current_user.is_librarian %}<a href="/duplicates" title="Duplicate Review"{% if request.url.path.startswith('/duplicates') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="13" height="13" rx="2"></rect><rect x="8" y="8" width="13" height="13" rx="2"></rect></svg><span>Duplicate Review</span></a>
              <a href="/bulk-match" title="Bulk Match"{% if request.url.path.startswith('/bulk-match') or request.url.path.startswith('/movies/bulk-match') or request.url.path.startswith('/shows/bulk-match') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h10M4 12h10M4 18h7"></path><path d="m16 15 2 2 4-5"></path></svg><span>Bulk Match</span></a>{% endif %}
            </div>
          </details>

          <details class="workspace-nav-section" data-workspace-section="more"{% if workspace_more_active %} open{% endif %}>
            <summary aria-label="More shortcuts">More</summary>
            <div class="workspace-nav-secondary">
              {% if current_user.is_librarian %}<a href="/settings" title="Settings"{% if request.url.path.startswith('/settings') %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"></path></svg><span>Settings</span></a>{% endif %}
              <a href="/help" title="Help"{% if request.url.path == '/help' %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M9.7 9a2.5 2.5 0 1 1 3.7 2.2c-.9.5-1.4 1.1-1.4 2.3"></path><path d="M12 17.5h.01"></path></svg><span>Help</span></a>
              <a href="/about" title="About"{% if request.url.path == '/about' %} class="active" aria-current="page"{% endif %}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M12 10v7M12 7h.01"></path></svg><span>About</span></a>
            </div>
          </details>
        </nav>'''
base = base[:nav_start] + workspace_nav + base[nav_end:]
base_path.write_text(base, encoding="utf-8")


js_path = ROOT / "app" / "static" / "workspace.js"
js = js_path.read_text(encoding="utf-8")
nav_logic_start = js.find("  const path = window.location.pathname;")
nav_logic_end = js.find("  const enhanceLibraryInspector =")
if nav_logic_start < 0 or nav_logic_end < 0 or nav_logic_end <= nav_logic_start:
    raise RuntimeError("Could not locate client-built Workspace navigation")
server_nav_logic = '''  const enhanceWorkspaceNavigation = () => {
    const panel = document.getElementById("site-menu-panel");
    if (!panel) return;
    const sections = [...panel.querySelectorAll(".workspace-nav-section")];
    sections.forEach((section) => {
      section.addEventListener("toggle", () => {
        if (!section.open) return;
        sections.forEach((other) => {
          if (other !== section) other.open = false;
        });
      });
    });
  };

'''
js = js[:nav_logic_start] + server_nav_logic + js[nav_logic_end:]
js = replace_once(js, "    enhanceNavigation();", "    enhanceWorkspaceNavigation();", "Workspace initializer")
js_path.write_text(js, encoding="utf-8")


css_path = ROOT / "app" / "static" / "workspace.css"
css = css_path.read_text(encoding="utf-8")
css = replace_once(
    css,
    "/* Keep the legacy navigation from flashing before Workspace enhancement runs. */\n",
    "/* The final Workspace navigation is server-rendered. Keep any accidental legacy panel hidden. */\n",
    "navigation flash comment",
)
css = replace_once(
    css,
    '''/* The old home-layout switcher is superseded by the Workspace shell. */
.home-layout-toggle {
  display: none !important;
}

''',
    "",
    "deprecated home-layout CSS",
)
css = replace_once(css, "  pointer-events: none;\n", "", "version badge pointer behavior")
collapsed_marker = '''@media (min-width: 981px) {
  body.has-app-sidebar.sidebar-collapsed .workspace-nav-section {
'''
collapsed_replacement = '''@media (min-width: 981px) {
  body.has-app-sidebar.sidebar-collapsed .workspace-nav-primary {
    padding-inline: 0;
  }

  body.has-app-sidebar.sidebar-collapsed .workspace-nav-section {
'''
css = replace_once(css, collapsed_marker, collapsed_replacement, "collapsed navigation")
css_path.write_text(css, encoding="utf-8")


test_path = ROOT / "tests" / "test_workspace_ui.py"
test_path.write_text('''from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class WorkspaceFoundationTests(unittest.TestCase):
    def test_08_alpha_version_and_workspace_assets_are_enabled(self):
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('APP_VERSION = "0.8.0-alpha.1"', main)
        self.assertIn("path='workspace.css'", base)
        self.assertIn("path='workspace.js'", base)

    def test_workspace_navigation_is_server_rendered_and_collapsible(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn('site-menu-panel workspace-nav-ready', base)
        self.assertIn('data-workspace-nav', base)
        for label in ("Dashboard", "Library", "Review", "Sources", "Activity"):
            self.assertIn(f"<span>{label}</span>", base)
        for href in ("/movies", "/shows", "/collections", "/favorites", "/duplicates", "/bulk-match"):
            self.assertIn(f'href="{href}"', base)
        for section in ("library", "review", "more"):
            self.assertIn(f'data-workspace-section="{section}"', base)
        self.assertIn("enhanceWorkspaceNavigation", script)
        self.assertNotIn("cloneLink", script)
        self.assertNotIn("replaceChildren(primary)", script)
        self.assertIn("sidebar-collapsed .workspace-nav-section", styles)
        self.assertIn("0.8 α", base)

    def test_workspace_removes_home_layout_switcher_from_shell(self):
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertNotIn('class="home-layout-toggle"', base)
        self.assertNotIn('action="/account/home-layout"', base)

    def test_library_inspector_preserves_full_detail_navigation(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector", script)
        self.assertIn("Open full details", script)
        self.assertIn("dblclick", script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key === "Enter"', script)

    def test_detail_workspace_adds_local_people_previews(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "static" / "workspace.css").read_text(encoding="utf-8")
        self.assertIn("enhanceCreditHoverCards", script)
        self.assertIn("workspace-person-popover", script)
        self.assertIn("Search library for this person", script)
        self.assertIn("workspace-person-popover", styles)
        self.assertIn("media-dossier .detail-page-head", styles)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")


doc_path = ROOT / "docs" / "WORKSPACE.md"
doc = doc_path.read_text(encoding="utf-8")
doc = doc.replace(
    "Primary work domains are Dashboard, Library, Review, Sources, and Activity. Existing capabilities remain available as secondary destinations beneath Library, Review, and System groupings.",
    "Primary work domains are Dashboard, Library, Review, Sources, and Activity. Existing capabilities remain available as collapsible secondary destinations beneath Library, Review, and More. The final navigation hierarchy is rendered by Jinja on the first response; JavaScript only coordinates interaction, so the shell does not repaint from a legacy menu after load.",
)
doc = doc.replace(
    "1. **W1 Foundation**: shared workspace styles, navigation hierarchy, contextual bulk-action toolbar, first persistent Library inspector.",
    "1. **W1 Foundation + stabilization**: server-rendered workspace shell, collapsible navigation hierarchy, intentional compact rail, contextual bulk-action toolbar, cohesive title dossier, local-library people previews, and the first persistent Library inspector.",
)
doc_path.write_text(doc, encoding="utf-8")

print("Workspace stabilization patch applied.")
