(() => {
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
