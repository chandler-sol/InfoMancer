(() => {
  const enhanceWorkspaceNavigation = () => {
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
        <button class="workspace-inspector-close" type="button" aria-label="Close inspector"></button>
      </div>
      <div class="workspace-inspector-body"></div>`;
    document.body.append(inspector);

    const body = inspector.querySelector(".workspace-inspector-body");
    const close = inspector.querySelector(".workspace-inspector-close");
    let selected = null;
    let selectedTitleId = "";
    let detailHref = "";
    let requestController = null;
    let rangeAnchorId = "";

    const interactive = (target) => target.closest("input, button, summary, details, form, select, textarea, .item-action-menu");
    const titleIdFor = (item) => {
      if (!item) return "";
      if (item.dataset.workspaceTitleId) return item.dataset.workspaceTitleId;
      const href = item.querySelector(".title-link, .cover-card-link")?.getAttribute("href") || "";
      return href.match(/\/titles\/(\d+)/)?.[1] || "";
    };
    const visibleItems = () => {
      const selector = libraryTable && !libraryTable.hidden ? ".library-title-row" : ".cover-card";
      return [...document.querySelectorAll(selector)].filter(item => titleIdFor(item));
    };
    const itemForTitle = (titleId) => visibleItems().find(item => titleIdFor(item) === String(titleId))
      || document.querySelector(`[data-workspace-title-id="${CSS.escape(String(titleId))}"]`);
    const choiceFor = (item) => item?.querySelector(".library-title-choice");

    const updateInspectorUrl = (titleId, mode = "push") => {
      const url = new URL(window.location.href);
      if (titleId) url.searchParams.set("inspect", titleId);
      else url.searchParams.delete("inspect");
      const state = {...(history.state || {}), workspaceInspectorTitleId: titleId || null};
      history[mode === "replace" ? "replaceState" : "pushState"](state, "", url.pathname + url.search + url.hash);
    };

    const closeInspector = ({historyMode = "replace"} = {}) => {
      requestController?.abort();
      selected?.classList.remove("workspace-selected");
      selected = null;
      selectedTitleId = "";
      detailHref = "";
      document.body.classList.remove("workspace-inspector-open");
      window.setTimeout(() => { inspector.hidden = true; }, 190);
      if (historyMode === "back" && history.state?.workspaceInspectorTitleId) history.back();
      else if (historyMode === "replace") updateInspectorUrl("", "replace");
    };

    const renderState = (message, className = "") => {
      body.innerHTML = `<div class="workspace-inspector-state ${className}"><span></span><p></p></div>`;
      body.querySelector("p").textContent = message;
    };

    const postInspectorAction = async (url, csrf) => {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {"X-CSRF-Token": csrf, "Content-Type": "application/x-www-form-urlencoded"},
        body: "",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    };

    const syncFavoriteUI = (titleId, favorite) => {
      document.querySelectorAll(`[data-workspace-title-id="${CSS.escape(String(titleId))}"]`).forEach(item => {
        item.querySelectorAll(".cover-favorite-button, .favorite-action").forEach(control => control.classList.toggle("active", favorite));
        const star = item.querySelector(".title-organization .favorite-star");
        if (star) star.classList.toggle("active", favorite);
      });
    };

    const renderInspectorEpisode = (file) => {
      const row = document.createElement("article");
      row.className = "workspace-inspector-episode";
      const code = document.createElement("span");
      code.className = "workspace-inspector-episode-code";
      code.textContent = file.episode_code || "File";
      const copy = document.createElement("span");
      copy.className = "workspace-inspector-episode-copy";
      const name = document.createElement("strong");
      name.textContent = file.episode_name || file.filename;
      name.title = file.filename || "";
      const facts = document.createElement("small");
      facts.textContent = [
        file.size_display,
        file.runtime_display,
        file.resolution_display,
        file.video_codec,
        file.audio_codec,
        file.dynamic_range,
      ].filter(Boolean).join(" · ");
      copy.append(name, facts);
      row.append(code, copy);
      return row;
    };

    const enhanceInspectorMedia = async (panel) => {
      const host = panel?.querySelector("[data-inspector-tv-seasons]");
      if (!host) return;
      const titleId = host.dataset.titleId;
      if (!titleId) return;
      try {
        const response = await fetch(`/api/titles/${encodeURIComponent(titleId)}/inspector-media`, {
          credentials: "same-origin",
          cache: "no-store",
          signal: requestController?.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (String(titleId) !== selectedTitleId || !host.isConnected) return;
        host.replaceChildren();
        if (!data.seasons?.length) {
          const empty = document.createElement("p");
          empty.className = "workspace-inspector-empty-state";
          empty.textContent = "No indexed seasons are attached to this title.";
          host.append(empty);
          return;
        }
        data.seasons.forEach((season) => {
          const shell = document.createElement("section");
          shell.className = "workspace-inspector-season";
          const trigger = document.createElement("button");
          trigger.type = "button";
          trigger.setAttribute("aria-expanded", "false");
          const label = document.createElement("strong");
          label.textContent = season.label;
          const meta = document.createElement("small");
          meta.textContent = `${season.file_count} file${season.file_count === 1 ? "" : "s"} · ${season.total_size_display}`;
          const chevron = document.createElement("span");
          chevron.className = "workspace-inspector-season-chevron";
          chevron.setAttribute("aria-hidden", "true");
          chevron.textContent = "⌄";
          const seasonBody = document.createElement("div");
          seasonBody.className = "workspace-inspector-season-body";
          seasonBody.hidden = true;
          seasonBody.dataset.loaded = "false";
          trigger.append(label, meta, chevron);
          shell.append(trigger, seasonBody);

          trigger.addEventListener("click", async () => {
            const opening = seasonBody.hidden;
            seasonBody.hidden = !opening;
            shell.classList.toggle("open", opening);
            trigger.setAttribute("aria-expanded", String(opening));
            if (!opening || seasonBody.dataset.loaded === "true" || seasonBody.dataset.loading === "true") return;
            seasonBody.dataset.loading = "true";
            const state = document.createElement("p");
            state.className = "workspace-inspector-season-state";
            state.textContent = `Loading ${season.label.toLowerCase()}…`;
            seasonBody.replaceChildren(state);
            try {
              const detailResponse = await fetch(
                `/api/titles/${encodeURIComponent(titleId)}/inspector-media/${encodeURIComponent(season.key)}`,
                {credentials: "same-origin", cache: "no-store", signal: requestController?.signal},
              );
              if (!detailResponse.ok) throw new Error(`HTTP ${detailResponse.status}`);
              const detail = await detailResponse.json();
              if (String(titleId) !== selectedTitleId || !seasonBody.isConnected) return;
              seasonBody.replaceChildren();
              detail.files.forEach((file) => seasonBody.append(renderInspectorEpisode(file)));
              if (!detail.files.length) {
                const empty = document.createElement("p");
                empty.className = "workspace-inspector-season-state";
                empty.textContent = "No indexed files are in this season.";
                seasonBody.append(empty);
              }
              const full = document.createElement("a");
              full.className = "workspace-inspector-season-link";
              full.href = season.season === null ? `/titles/${titleId}` : `/titles/${titleId}#season-${season.season}`;
              full.textContent = `Open ${season.label} in full details →`;
              seasonBody.append(full);
              seasonBody.dataset.loaded = "true";
            } catch (error) {
              if (error.name !== "AbortError") {
                state.textContent = "Season files could not be loaded. Open full details to continue.";
                seasonBody.replaceChildren(state);
              }
            } finally {
              seasonBody.dataset.loading = "false";
            }
          });
          host.append(shell);
        });
      } catch (error) {
        if (error.name !== "AbortError" && host.isConnected) {
          const failed = document.createElement("p");
          failed.className = "workspace-inspector-empty-state";
          failed.textContent = "Season groups could not be loaded. Open full details to continue.";
          host.replaceChildren(failed);
        }
      }
    };

    const enhanceInspectorActions = () => {
      const panel = body.querySelector("[data-workspace-inspector-panel]");
      if (!panel) return;
      detailHref = panel.dataset.detailHref || detailHref;
      const csrf = panel.querySelector("[data-workspace-csrf]")?.value || "";
      const favorite = panel.querySelector("[data-workspace-favorite]");
      favorite?.addEventListener("click", async () => {
        favorite.disabled = true;
        try {
          const data = await postInspectorAction(`/api/titles/${selectedTitleId}/favorite`, csrf);
          favorite.classList.toggle("active", data.favorite);
          favorite.setAttribute("aria-pressed", String(Boolean(data.favorite)));
          favorite.title = data.favorite ? "Remove from favorites" : "Add to favorites";
          syncFavoriteUI(selectedTitleId, Boolean(data.favorite));
        } catch (_error) {
          favorite.classList.add("save-error");
          window.setTimeout(() => favorite.classList.remove("save-error"), 1200);
        } finally {
          favorite.disabled = false;
        }
      });
      panel.querySelectorAll("[data-workspace-tag]").forEach(tag => {
        tag.addEventListener("click", async () => {
          tag.disabled = true;
          try {
            const data = await postInspectorAction(
              `/api/titles/${selectedTitleId}/tags/${tag.dataset.workspaceTag}`, csrf,
            );
            tag.classList.toggle("active", data.selected);
            tag.setAttribute("aria-pressed", String(Boolean(data.selected)));
          } catch (_error) {
            tag.classList.add("save-error");
            window.setTimeout(() => tag.classList.remove("save-error"), 1200);
          } finally {
            tag.disabled = false;
          }
        });
      });
      enhanceInspectorMedia(panel);
    };

    const inspectTitle = async (titleId, item = null, historyMode = "push") => {
      if (!titleId) return;
      requestController?.abort();
      requestController = new AbortController();
      selected?.classList.remove("workspace-selected");
      selected = item || itemForTitle(titleId);
      selected?.classList.add("workspace-selected");
      selectedTitleId = String(titleId);
      detailHref = `/titles/${titleId}`;
      inspector.hidden = false;
      renderState("Loading title details…", "loading");
      requestAnimationFrame(() => document.body.classList.add("workspace-inspector-open"));
      if (historyMode !== "none") updateInspectorUrl(titleId, historyMode);
      try {
        const response = await fetch(`/library/inspector/${encodeURIComponent(titleId)}`, {
          credentials: "same-origin",
          cache: "no-store",
          signal: requestController.signal,
          headers: {"X-Workspace-Inspector": "1"},
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        body.innerHTML = await response.text();
        enhanceInspectorActions();
      } catch (error) {
        if (error.name !== "AbortError") renderState("Inspector details could not be loaded. Open the full title page or try again.", "error");
      }
    };

    const toggleSelection = (item, force = null) => {
      const choice = choiceFor(item);
      if (!choice) return;
      choice.checked = force === null ? !choice.checked : force;
      choice.dispatchEvent(new Event("change", {bubbles: true}));
      rangeAnchorId = titleIdFor(item);
    };

    const selectRange = (item) => {
      const items = visibleItems();
      const targetId = titleIdFor(item);
      const anchorId = rangeAnchorId || selectedTitleId || targetId;
      const start = items.findIndex(candidate => titleIdFor(candidate) === anchorId);
      const finish = items.findIndex(candidate => titleIdFor(candidate) === targetId);
      if (start < 0 || finish < 0) {
        toggleSelection(item, true);
        return;
      }
      const [low, high] = start < finish ? [start, finish] : [finish, start];
      items.slice(low, high + 1).forEach(candidate => {
        const choice = choiceFor(candidate);
        if (choice && !choice.checked) {
          choice.checked = true;
          choice.dispatchEvent(new Event("change", {bubbles: true}));
        }
      });
      rangeAnchorId = targetId;
    };

    document.addEventListener("mousedown", (event) => {
      const item = event.target.closest(".library-title-row, .cover-card");
      if (item && event.shiftKey && !interactive(event.target)) event.preventDefault();
    });

    document.addEventListener("click", (event) => {
      const item = event.target.closest(".library-title-row, .cover-card");
      if (!item || interactive(event.target)) return;
      const titleId = titleIdFor(item);
      if (!titleId) return;
      if (event.metaKey || event.ctrlKey) {
        event.preventDefault();
        toggleSelection(item);
        return;
      }
      if (event.shiftKey) {
        event.preventDefault();
        selectRange(item);
        return;
      }
      const titleLink = event.target.closest(".title-link, .cover-card-link");
      if (titleLink || !interactive(event.target)) {
        event.preventDefault();
        rangeAnchorId = titleId;
        if (String(titleId) === selectedTitleId) {
          closeInspector({historyMode: history.state?.workspaceInspectorTitleId ? "back" : "replace"});
          return;
        }
        inspectTitle(titleId, item, selectedTitleId ? "replace" : "push");
      }
    });

    document.addEventListener("dblclick", (event) => {
      const item = event.target.closest(".library-title-row, .cover-card");
      if (!item || interactive(event.target)) return;
      const titleId = titleIdFor(item);
      if (titleId) window.location.assign(`/titles/${titleId}`);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && selectedTitleId) {
        event.preventDefault();
        closeInspector({historyMode: history.state?.workspaceInspectorTitleId ? "back" : "replace"});
        return;
      }
      if (event.key === "Enter" && selectedTitleId && detailHref && !event.target.matches("input,textarea,select,button,a")) {
        window.location.assign(detailHref);
        return;
      }
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && selectedTitleId && !event.target.matches("input,textarea,select")) {
        const items = visibleItems();
        const current = items.findIndex(item => titleIdFor(item) === selectedTitleId);
        if (current < 0 || !items.length) return;
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        const next = Math.min(items.length - 1, Math.max(0, current + offset));
        if (next !== current) inspectTitle(titleIdFor(items[next]), items[next], "replace");
      }
    });

    close.addEventListener("click", () => closeInspector({historyMode: history.state?.workspaceInspectorTitleId ? "back" : "replace"}));
    window.addEventListener("popstate", () => {
      const titleId = new URL(window.location.href).searchParams.get("inspect");
      if (titleId) inspectTitle(titleId, itemForTitle(titleId), "none");
      else if (selectedTitleId) closeInspector({historyMode: "none"});
    });
    document.addEventListener("infomancer:library-results-updated", () => {
      if (selectedTitleId && !itemForTitle(selectedTitleId)) closeInspector({historyMode: "replace"});
    });

    const initialTitleId = new URL(window.location.href).searchParams.get("inspect");
    if (initialTitleId) inspectTitle(initialTitleId, itemForTitle(initialTitleId), "none");
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

    const closeNow = () => {
      window.clearTimeout(openTimer);
      window.clearTimeout(closeTimer);
      popover.hidden = true;
      activeLink = null;
    };

    const scheduleClose = () => {
      window.clearTimeout(openTimer);
      window.clearTimeout(closeTimer);
      closeTimer = window.setTimeout(closeNow, 120);
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
    document.addEventListener("pointerdown", (event) => {
      if (popover.hidden || popover.contains(event.target) || activeLink?.contains(event.target)) return;
      closeNow();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !popover.hidden) closeNow();
    });
    window.addEventListener("resize", () => activeLink && position(activeLink));
    window.addEventListener("scroll", () => activeLink && position(activeLink), true);
  };

  const initialize = () => {
    if (document.querySelector(".library-table, #cover-library")) {
      document.documentElement.classList.add("library-view-ready");
    }
    enhanceWorkspaceNavigation();
    enhanceLibraryInspector();
    enhanceCreditHoverCards();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();