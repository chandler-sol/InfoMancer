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
    link.title = label;
    return link;
  };

  const markActive = (link, active) => {
    if (!link) return;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  };

  const startsAny = (prefixes) => prefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));

  const ensureAlphaBadge = () => {
    const brand = document.querySelector(".brand");
    if (!brand || brand.querySelector(".workspace-nav-alpha")) return;
    const alpha = document.createElement("span");
    alpha.className = "workspace-nav-alpha";
    alpha.textContent = "0.8 α";
    alpha.title = "InfoMancer 0.8 Alpha Workspace";
    alpha.setAttribute("aria-label", "Version 0.8 Alpha Workspace");
    brand.append(alpha);
  };

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

    const makeSection = (title, hrefs, openWhen) => {
      const links = hrefs.map((href) => byHref.get(href)).filter(Boolean);
      if (!links.length) return null;
      const section = document.createElement("details");
      section.className = "workspace-nav-section";
      section.dataset.workspaceSection = title.toLowerCase();
      section.open = Boolean(openWhen);

      const summary = document.createElement("summary");
      summary.textContent = title;
      summary.setAttribute("aria-label", `${title} shortcuts`);
      const list = document.createElement("div");
      list.className = "workspace-nav-secondary";
      links.forEach((link) => {
        link.title = link.querySelector("span")?.textContent?.trim() || link.textContent.trim();
        list.append(link);
      });
      section.append(summary, list);
      return section;
    };

    const librarySection = makeSection(
      "Library",
      ["/movies", "/shows", "/collections", "/libraries", "/favorites"],
      startsAny(["/library", "/movies", "/shows", "/titles", "/files", "/collections", "/libraries", "/favorites"]),
    );
    const reviewSection = makeSection(
      "Review",
      ["/library-health", "/duplicates", "/bulk-match"],
      startsAny(["/library-health", "/duplicates", "/bulk-match"]),
    );
    const moreSection = makeSection(
      "More",
      ["/settings", "/help", "/about"],
      startsAny(["/settings", "/help", "/about"]),
    );

    panel.replaceChildren(primary);
    [librarySection, reviewSection, moreSection].filter(Boolean).forEach((section) => panel.append(section));
    panel.classList.add("workspace-nav-ready");
    panel.dataset.workspaceReady = "1";

    panel.querySelectorAll(".workspace-nav-section").forEach((section) => {
      section.addEventListener("toggle", () => {
        if (!section.open) return;
        panel.querySelectorAll(".workspace-nav-section[open]").forEach((other) => {
          if (other !== section) other.open = false;
        });
      });
    });

    ensureAlphaBadge();
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

  const enhanceCreditHoverCards = () => {
    const personLinks = [...document.querySelectorAll('.movie-credits a[href^="/library?q="], .episode-credits a[href^="/library?q="]')];
    if (!personLinks.length) return;

    const popover = document.createElement("aside");
    popover.className = "workspace-person-popover";
    popover.hidden = true;
    popover.setAttribute("aria-live", "polite");
    document.body.append(popover);

    const cache = new Map();
    let openTimer = 0;
    let closeTimer = 0;
    let activeLink = null;

    const roleFor = (link) => {
      const creditRow = link.closest(".movie-credits > div");
      const label = creditRow?.querySelector("strong")?.textContent?.replace(":", "").trim();
      if (label) return label;
      const episodeText = link.closest(".episode-credits span")?.textContent || "";
      if (episodeText.startsWith("Directed by")) return "Director";
      if (episodeText.startsWith("Written by")) return "Writer";
      return "Person";
    };

    const position = (link) => {
      const rect = link.getBoundingClientRect();
      const width = Math.min(330, window.innerWidth - 24);
      let left = rect.left;
      if (left + width > window.innerWidth - 12) left = window.innerWidth - width - 12;
      left = Math.max(12, left);
      const estimatedHeight = Math.min(300, popover.offsetHeight || 220);
      let top = rect.bottom + 9;
      if (top + estimatedHeight > window.innerHeight - 12) top = Math.max(12, rect.top - estimatedHeight - 9);
      popover.style.left = `${left}px`;
      popover.style.top = `${top}px`;
    };

    const render = (link, items = null, failed = false) => {
      popover.replaceChildren();
      const head = document.createElement("div");
      head.className = "workspace-person-popover-head";
      const copy = document.createElement("span");
      const role = document.createElement("small");
      role.textContent = roleFor(link);
      const name = document.createElement("strong");
      name.textContent = link.textContent.trim();
      copy.append(role, name);
      head.append(copy);
      popover.append(head);

      if (items === null && !failed) {
        const loading = document.createElement("p");
        loading.className = "workspace-person-popover-state";
        loading.textContent = "Finding titles in your library…";
        popover.append(loading);
      } else if (items?.length) {
        const label = document.createElement("small");
        label.className = "workspace-person-popover-label";
        label.textContent = "IN YOUR LIBRARY";
        const list = document.createElement("div");
        list.className = "workspace-person-title-list";
        items.slice(0, 4).forEach((item) => {
          const title = document.createElement("a");
          title.href = item.href;
          title.textContent = item.title;
          list.append(title);
        });
        popover.append(label, list);
      } else {
        const empty = document.createElement("p");
        empty.className = "workspace-person-popover-state";
        empty.textContent = failed ? "Preview unavailable. The library search still works." : "No additional local titles found in this preview.";
        popover.append(empty);
      }

      const search = document.createElement("a");
      search.className = "workspace-person-search";
      search.href = link.href;
      search.textContent = "Search library for this person →";
      popover.append(search);
      popover.hidden = false;
      position(link);
    };

    const load = async (link) => {
      const key = link.href;
      if (cache.has(key)) {
        if (activeLink === link) render(link, cache.get(key));
        return;
      }
      try {
        const response = await fetch(key, { credentials: "same-origin", headers: { "X-Workspace-Preview": "person" } });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const html = await response.text();
        const doc = new DOMParser().parseFromString(html, "text/html");
        const seen = new Set();
        const items = [];
        doc.querySelectorAll(".title-link, .cover-card-link").forEach((candidate) => {
          const rawHref = candidate.getAttribute("href");
          const href = rawHref ? new URL(rawHref, window.location.origin).href : "";
          const title = candidate.querySelector("strong")?.textContent?.trim() || candidate.textContent?.trim();
          if (!href || !title || seen.has(href)) return;
          seen.add(href);
          items.push({ href, title });
        });
        cache.set(key, items);
        if (activeLink === link) render(link, items);
      } catch (_error) {
        if (activeLink === link) render(link, [], true);
      }
    };

    const show = (link) => {
      window.clearTimeout(closeTimer);
      activeLink = link;
      render(link, cache.get(link.href) ?? null);
      if (!cache.has(link.href)) load(link);
    };

    const scheduleClose = () => {
      window.clearTimeout(closeTimer);
      closeTimer = window.setTimeout(() => {
        popover.hidden = true;
        activeLink = null;
      }, 140);
    };

    personLinks.forEach((link) => {
      link.classList.add("workspace-person-link");
      link.title = "Preview this person's titles in your library";
      link.addEventListener("pointerenter", () => {
        window.clearTimeout(openTimer);
        window.clearTimeout(closeTimer);
        openTimer = window.setTimeout(() => show(link), 180);
      });
      link.addEventListener("pointerleave", scheduleClose);
      link.addEventListener("focus", () => show(link));
      link.addEventListener("blur", scheduleClose);
    });

    popover.addEventListener("pointerenter", () => window.clearTimeout(closeTimer));
    popover.addEventListener("pointerleave", scheduleClose);
    window.addEventListener("resize", () => activeLink && position(activeLink));
    window.addEventListener("scroll", () => activeLink && position(activeLink), true);
  };

  const initialize = () => {
    enhanceNavigation();
    enhanceLibraryInspector();
    enhanceCreditHoverCards();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
