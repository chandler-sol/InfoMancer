from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path}, found {count}: {old[:100]!r}")
    write(path, content.replace(old, new, 1))


replace_once(
    "app/main.py",
    'APP_VERSION = "0.7.0-alpha.1"',
    'APP_VERSION = "0.8.0-alpha.1"',
)

replace_once(
    "app/templates/base.html",
    '  <link rel="stylesheet" href="{{ url_for(\'static\', path=\'modern.css\') }}?v={{ static_version }}">\n',
    '  <link rel="stylesheet" href="{{ url_for(\'static\', path=\'modern.css\') }}?v={{ static_version }}">\n'
    '  <link rel="stylesheet" href="{{ url_for(\'static\', path=\'workspace.css\') }}?v={{ static_version }}">\n',
)
replace_once(
    "app/templates/base.html",
    '  <script src="{{ url_for(\'static\', path=\'multipart-submit.js\') }}?v={{ static_version }}" defer></script>\n',
    '  <script src="{{ url_for(\'static\', path=\'multipart-submit.js\') }}?v={{ static_version }}" defer></script>\n'
    '  <script src="{{ url_for(\'static\', path=\'workspace.js\') }}?v={{ static_version }}" defer></script>\n',
)

write(
    "app/static/workspace.css",
    r''':root {
  --im-radius-xs: 6px;
  --im-radius-sm: 9px;
  --im-radius-md: 12px;
  --im-radius-lg: 16px;
  --im-shadow-raised: 0 18px 60px rgba(0, 0, 0, .38);
  --im-inspector-width: min(380px, 34vw);
  --im-motion: 180ms cubic-bezier(.2, .8, .2, 1);
}

/* Workspace navigation hierarchy */
.site-menu-panel.workspace-nav-ready {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-bottom: 16px;
}

.workspace-nav-primary,
.workspace-nav-secondary {
  display: grid;
  gap: 4px;
}

.workspace-nav-primary {
  padding: 4px 8px 10px;
}

.workspace-nav-primary > a {
  min-height: 43px;
  border-radius: var(--im-radius-sm);
  font-weight: 700;
}

.workspace-nav-primary > a.active {
  background: rgba(185, 245, 66, .10);
  color: var(--text);
  box-shadow: inset 3px 0 0 var(--lime);
}

.workspace-nav-section {
  margin: 4px 8px 0;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

.workspace-nav-section > strong {
  display: block;
  margin: 0 10px 6px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .15em;
  text-transform: uppercase;
}

.workspace-nav-secondary a {
  min-height: 34px;
  padding-block: 7px;
  border-radius: var(--im-radius-xs);
  font-size: 13px;
}

.workspace-nav-secondary a svg {
  width: 16px;
  height: 16px;
  opacity: .72;
}

.workspace-nav-secondary a.active {
  color: var(--text);
  background: rgba(255, 255, 255, .055);
}

.workspace-nav-alpha {
  display: inline-flex;
  width: fit-content;
  margin: 10px 18px 2px;
  padding: 3px 7px;
  border: 1px solid rgba(81, 214, 230, .28);
  border-radius: 999px;
  color: var(--cyan);
  background: rgba(81, 214, 230, .06);
  font-size: 9px;
  font-weight: 800;
  letter-spacing: .12em;
  text-transform: uppercase;
}

/* Library selection now behaves like a contextual app toolbar. */
.library-selection-actions:not([hidden]) {
  position: sticky;
  z-index: 24;
  bottom: 16px;
  display: flex;
  width: fit-content;
  max-width: calc(100vw - 48px);
  margin: 14px auto;
  padding: 9px 10px;
  gap: 8px;
  border: 1px solid #3a4857;
  border-radius: var(--im-radius-md);
  background: rgba(18, 24, 32, .94);
  box-shadow: var(--im-shadow-raised);
  backdrop-filter: blur(16px);
}

.library-title-row.workspace-selected > td,
.cover-card.workspace-selected {
  background-color: rgba(81, 214, 230, .055);
}

.library-title-row.workspace-selected > td:first-child {
  box-shadow: inset 3px 0 0 var(--cyan);
}

.cover-card.workspace-selected {
  outline: 2px solid rgba(81, 214, 230, .65);
  outline-offset: 2px;
}

/* Persistent inspector drawer */
.workspace-inspector {
  position: fixed;
  z-index: 70;
  top: 68px;
  right: 0;
  bottom: 0;
  width: var(--im-inspector-width);
  min-width: 320px;
  overflow: auto;
  border-left: 1px solid #33404d;
  background: rgba(13, 18, 24, .985);
  box-shadow: -24px 0 70px rgba(0, 0, 0, .34);
  transform: translateX(102%);
  transition: transform var(--im-motion);
}

.workspace-inspector[hidden] {
  display: block;
  visibility: hidden;
  pointer-events: none;
}

body.workspace-inspector-open .workspace-inspector {
  visibility: visible;
  pointer-events: auto;
  transform: translateX(0);
}

.workspace-inspector-head {
  position: sticky;
  z-index: 2;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 54px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(13, 18, 24, .94);
  backdrop-filter: blur(14px);
}

.workspace-inspector-head span {
  color: var(--muted);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .16em;
  text-transform: uppercase;
}

.workspace-inspector-close {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  font-size: 22px;
}

.workspace-inspector-body {
  padding: 20px;
}

.workspace-inspector-art {
  width: 118px;
  aspect-ratio: 2 / 3;
  margin-bottom: 18px;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--im-radius-md);
  background: var(--surface);
}

.workspace-inspector-art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.workspace-inspector-kicker {
  margin: 0 0 4px;
  color: var(--cyan);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .14em;
  text-transform: uppercase;
}

.workspace-inspector-title {
  margin: 0;
  font: 600 30px/1.05 var(--serif);
  letter-spacing: -.025em;
}

.workspace-inspector-meta {
  display: grid;
  gap: 0;
  margin: 20px 0;
  border-top: 1px solid var(--line);
}

.workspace-inspector-meta div {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 12px;
  padding: 11px 0;
  border-bottom: 1px solid var(--line);
}

.workspace-inspector-meta dt {
  color: var(--muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .08em;
}

.workspace-inspector-meta dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.workspace-inspector-actions {
  display: grid;
  gap: 8px;
}

.workspace-inspector-actions .button {
  display: flex;
  justify-content: center;
  border-radius: var(--im-radius-sm);
}

.workspace-inspector-action-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.workspace-inspector-action-list a {
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: var(--im-radius-sm);
  color: var(--text);
  text-decoration: none;
  font-size: 12px;
}

.workspace-inspector-action-list a:hover {
  border-color: #536375;
  background: rgba(255, 255, 255, .04);
}

.workspace-inspector-hint {
  margin-top: 18px;
  color: var(--muted);
  font-size: 11px;
}

@media (min-width: 1180px) {
  body.workspace-inspector-open .shell {
    transition: padding-right var(--im-motion);
    padding-right: calc(var(--im-inspector-width) + 28px);
    max-width: 1600px;
  }
}

@media (max-width: 900px) {
  :root { --im-inspector-width: 100vw; }
  .workspace-inspector {
    top: auto;
    left: 0;
    width: 100%;
    min-width: 0;
    max-height: 72vh;
    border-top: 1px solid #33404d;
    border-left: 0;
    border-radius: 18px 18px 0 0;
    transform: translateY(102%);
  }
  body.workspace-inspector-open .workspace-inspector {
    transform: translateY(0);
  }
  .library-selection-actions:not([hidden]) {
    overflow-x: auto;
    justify-content: flex-start;
    margin-inline: 8px;
  }
}
''',
)

write(
    "app/static/workspace.js",
    r'''(() => {
  const path = window.location.pathname;

  const cloneLink = (source, href, label) => {
    if (!source) return null;
    const link = source.cloneNode(true);
    link.href = href;
    const text = link.querySelector("span");
    if (text) text.textContent = label;
    link.classList.remove("active");
    link.removeAttribute("aria-current");
    return link;
  };

  const markActive = (link, active) => {
    if (!link) return;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  };

  const startsAny = (prefixes) => prefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));

  const enhanceNavigation = () => {
    const panel = document.getElementById("site-menu-panel");
    if (!panel || panel.dataset.workspaceReady === "1") return;

    const originals = [...panel.querySelectorAll(":scope > a")];
    if (!originals.length) return;
    const byHref = new Map(originals.map((link) => [new URL(link.href, window.location.origin).pathname, link]));
    const source = (...hrefs) => hrefs.map((href) => byHref.get(href)).find(Boolean) || originals[0];

    const primary = document.createElement("div");
    primary.className = "workspace-nav-primary";
    const dashboard = cloneLink(source("/"), "/", "Dashboard");
    const library = cloneLink(source("/movies", "/shows"), "/library", "Library");
    const review = cloneLink(source("/library-health", "/duplicates"), "/library-health", "Review");
    const sources = cloneLink(source("/settings"), "/sources", "Sources");
    const activity = cloneLink(source("/activity"), "/activity", "Activity");
    markActive(dashboard, path === "/");
    markActive(library, startsAny(["/library", "/movies", "/shows", "/titles", "/files", "/collections", "/libraries", "/favorites"]));
    markActive(review, startsAny(["/library-health", "/duplicates", "/bulk-match"]));
    markActive(sources, startsAny(["/sources"]));
    markActive(activity, startsAny(["/activity", "/announcements"]));
    [dashboard, library, review, sources, activity].filter(Boolean).forEach((link) => primary.append(link));

    const makeSection = (title, hrefs) => {
      const links = hrefs.map((href) => byHref.get(href)).filter(Boolean);
      if (!links.length) return null;
      const section = document.createElement("section");
      section.className = "workspace-nav-section";
      const heading = document.createElement("strong");
      heading.textContent = title;
      const list = document.createElement("div");
      list.className = "workspace-nav-secondary";
      links.forEach((link) => list.append(link));
      section.append(heading, list);
      return section;
    };

    const librarySection = makeSection("Library", ["/movies", "/shows", "/collections", "/libraries", "/favorites"]);
    const reviewSection = makeSection("Review", ["/library-health", "/duplicates", "/bulk-match"]);
    const systemSection = makeSection("System", ["/announcements", "/settings", "/help", "/about"]);
    const alpha = document.createElement("span");
    alpha.className = "workspace-nav-alpha";
    alpha.textContent = "0.8 Alpha Workspace";

    panel.replaceChildren(alpha, primary);
    [librarySection, reviewSection, systemSection].filter(Boolean).forEach((section) => panel.append(section));
    panel.classList.add("workspace-nav-ready");
    panel.dataset.workspaceReady = "1";
  };

  const enhanceLibraryInspector = () => {
    const libraryTable = document.querySelector(".library-table");
    const coverLibrary = document.getElementById("cover-library");
    if (!libraryTable && !coverLibrary) return;

    const inspector = document.createElement("aside");
    inspector.id = "workspace-inspector";
    inspector.className = "workspace-inspector";
    inspector.hidden = true;
    inspector.setAttribute("aria-label", "Selected library item");
    inspector.innerHTML = `
      <div class="workspace-inspector-head">
        <span>Inspector</span>
        <button class="workspace-inspector-close" type="button" aria-label="Close inspector">×</button>
      </div>
      <div class="workspace-inspector-body"></div>`;
    document.body.append(inspector);

    const body = inspector.querySelector(".workspace-inspector-body");
    const close = inspector.querySelector(".workspace-inspector-close");
    let selected = null;
    let detailHref = "";

    const interactive = (target) => target.closest("input, button, summary, details, form, select, textarea, .item-action-menu");
    const titleLinkFor = (item) => item.querySelector(".title-link, .cover-card-link");

    const value = (item, selector) => item.querySelector(selector)?.textContent?.trim() || "";

    const closeInspector = () => {
      selected?.classList.remove("workspace-selected");
      selected = null;
      detailHref = "";
      document.body.classList.remove("workspace-inspector-open");
      window.setTimeout(() => { inspector.hidden = true; }, 190);
    };

    const metaRow = (label, content) => {
      if (!content) return null;
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const definition = document.createElement("dd");
      term.textContent = label;
      definition.textContent = content;
      row.append(term, definition);
      return row;
    };

    const inspect = (item) => {
      const link = titleLinkFor(item);
      if (!link) return;
      selected?.classList.remove("workspace-selected");
      selected = item;
      selected.classList.add("workspace-selected");
      detailHref = link.href;

      const title = value(item, ".title-link") || value(item, ".cover-card-link > strong") || "Library item";
      const kind = value(item, ".kind") || (item.querySelector('[data-kind="tv"]') ? "TV" : item.querySelector('[data-kind="movie"]') ? "Movie" : "Media");
      const match = value(item, ".match-cell .matched") || value(item, ".mobile-title-meta .matched") || (value(item, ".match-cell .muted") ? "Unmatched" : "");
      const pathValue = value(item, ".library-file-path");
      const coverMeta = value(item, ".cover-card-meta");
      const organization = value(item, ".title-organization");
      const image = item.querySelector("img");

      body.replaceChildren();
      if (image?.src) {
        const art = document.createElement("div");
        art.className = "workspace-inspector-art";
        const img = document.createElement("img");
        img.src = image.src;
        img.alt = "";
        art.append(img);
        body.append(art);
      }
      const kicker = document.createElement("p");
      kicker.className = "workspace-inspector-kicker";
      kicker.textContent = kind;
      const heading = document.createElement("h2");
      heading.className = "workspace-inspector-title";
      heading.textContent = title;
      body.append(kicker, heading);

      const meta = document.createElement("dl");
      meta.className = "workspace-inspector-meta";
      [
        metaRow("Match", match),
        metaRow("Details", coverMeta),
        metaRow("Organize", organization),
        metaRow("Location", pathValue),
      ].filter(Boolean).forEach((row) => meta.append(row));
      if (meta.children.length) body.append(meta);

      const actions = document.createElement("div");
      actions.className = "workspace-inspector-actions";
      const open = document.createElement("a");
      open.className = "button primary";
      open.href = detailHref;
      open.textContent = "Open full details";
      actions.append(open);

      const links = [...item.querySelectorAll(".item-action-menu a")].slice(0, 6);
      if (links.length) {
        const list = document.createElement("div");
        list.className = "workspace-inspector-action-list";
        links.forEach((original) => {
          const action = document.createElement("a");
          action.href = original.href;
          action.textContent = original.textContent.trim();
          if (original.hasAttribute("data-organize-dialog")) action.setAttribute("data-organize-dialog", "");
          list.append(action);
        });
        actions.append(list);
      }
      body.append(actions);

      const hint = document.createElement("p");
      hint.className = "workspace-inspector-hint";
      hint.textContent = "Single-click inspects. Double-click or press Enter to open full details.";
      body.append(hint);

      inspector.hidden = false;
      requestAnimationFrame(() => document.body.classList.add("workspace-inspector-open"));
    };

    document.addEventListener("click", (event) => {
      const titleLink = event.target.closest(".title-link, .cover-card-link");
      if (titleLink && !event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) {
        const item = titleLink.closest(".library-title-row, .cover-card");
        if (item) {
          event.preventDefault();
          inspect(item);
          return;
        }
      }
      const item = event.target.closest(".library-title-row, .cover-card");
      if (item && !interactive(event.target)) inspect(item);
    });

    document.addEventListener("dblclick", (event) => {
      const item = event.target.closest(".library-title-row, .cover-card");
      if (!item || interactive(event.target)) return;
      const link = titleLinkFor(item);
      if (link) window.location.assign(link.href);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && selected) closeInspector();
      if (event.key === "Enter" && selected && detailHref && !event.target.matches("input,textarea,select,button,a")) {
        window.location.assign(detailHref);
      }
    });

    close.addEventListener("click", closeInspector);
  };

  document.addEventListener("DOMContentLoaded", () => {
    enhanceNavigation();
    enhanceLibraryInspector();
  });
})();
''',
)

write(
    "docs/WORKSPACE.md",
    '''# InfoMancer Workspace

InfoMancer 0.8 starts the transition from a page-oriented management website to a persistent media-operations workspace.

## Product rules

- Preserve context. Selecting media should not immediately replace the working view.
- Single click inspects. Double click or Enter opens full details.
- Keep background work visible without forcing navigation.
- Small actions belong in popovers/dialogs, medium actions in drawers, and deep workflows in full workspace views.
- Every important state keeps a real URL and progressive server-rendered fallback.
- Avoid a framework rewrite. FastAPI and Jinja remain the application foundation.

## Navigation model

Primary work domains are Dashboard, Library, Review, Sources, and Activity. Existing capabilities remain available as secondary destinations beneath Library, Review, and System groupings.

## Workspace phases

1. **W1 Foundation**: shared workspace styles, navigation hierarchy, contextual bulk-action toolbar, first persistent Library inspector.
2. **W2 Library**: server-backed inspector partials, richer file/edition/quality information, history-aware selection state, instantaneous favorite/tag actions.
3. **W3 Review**: unified queue for MIE findings, duplicates, unmatched media, missing episodes, metadata issues, and quality decisions.
4. **W4 Interaction**: reusable drawers, dialogs, toasts, partial navigation, keyboard shortcuts, and command palette.
5. **W5 Saved Views**: named filter/sort workspaces that can be pinned to Library and Dashboard.
6. **W6 Operations**: generalized operation history and reversible actions where filesystem semantics permit safe undo.

W1 intentionally uses the existing rendered Library DOM as its inspector data source. W2 should replace that prototype with a dedicated read-only inspector endpoint/partial so the panel can expose richer metadata without duplicating page logic in JavaScript.
''',
)

write(
    "tests/test_workspace_ui.py",
    '''from __future__ import annotations

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

    def test_workspace_navigation_keeps_core_domains_and_secondary_destinations(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        for label in ("Dashboard", "Library", "Review", "Sources", "Activity"):
            self.assertIn(f'"{label}"', script)
        for href in ("/movies", "/shows", "/collections", "/favorites", "/duplicates", "/bulk-match"):
            self.assertIn(f'"{href}"', script)

    def test_library_inspector_preserves_full_detail_navigation(self):
        script = (ROOT / "app" / "static" / "workspace.js").read_text(encoding="utf-8")
        self.assertIn("workspace-inspector", script)
        self.assertIn("Open full details", script)
        self.assertIn("dblclick", script)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key === "Enter"', script)


if __name__ == "__main__":
    unittest.main()
''',
)

print("0.8 workspace foundation staged successfully")
