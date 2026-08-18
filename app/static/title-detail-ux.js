(() => {
  const ready = (callback) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, {once: true});
    } else {
      callback();
    }
  };

  ready(() => {
    const dossier = document.querySelector(".media-dossier");
    const titleMatch = window.location.pathname.match(/^\/titles\/(\d+)\/?$/);
    if (!dossier || !titleMatch) return;

    const titleId = titleMatch[1];
    const workflowDialog = document.getElementById("organize-dialog");
    const workflowBody = document.getElementById("organize-dialog-body");
    const terminalStatuses = new Set(["complete", "error", "failed", "cancelled"]);
    const runningStatuses = new Set(["queued", "starting", "running"]);
    const pollers = new Set();
    let workflowKind = "";
    let workflowUrl = window.location.href;
    let workflowOpener = null;

    /* ------------------------------------------------------------------------ */
    /* Shared title-action utilities                                            */
    /* ------------------------------------------------------------------------ */

    const toast = document.createElement("div");
    toast.className = "title-action-toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    document.body.append(toast);
    let toastTimer = 0;

    const showToast = (message, tone = "good") => {
      if (!message) return;
      window.clearTimeout(toastTimer);
      toast.textContent = message;
      toast.className = `title-action-toast ${tone}`;
      requestAnimationFrame(() => toast.classList.add("show"));
      toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2800);
    };

    window.addEventListener("infomancer:title-toast", (event) => {
      if (event.detail?.message) showToast(event.detail.message, event.detail.tone || "good");
    });

    const fetchJson = async (url, options = {}) => {
      const headers = new Headers(options.headers || {});
      headers.set("Accept", "application/json");
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
        headers,
      });
      let data = null;
      try {
        data = await response.json();
      } catch (_error) {}
      if (!response.ok) {
        const error = new Error(data?.detail || `HTTP ${response.status}`);
        error.status = response.status;
        error.data = data;
        throw error;
      }
      return data || {};
    };

    const fetchDocument = async (url, options = {}) => {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const html = await response.text();
      return {
        response,
        document: new DOMParser().parseFromString(html, "text/html"),
      };
    };

    const originalButtonLabel = (button, fallback = "") => {
      if (!button) return fallback;
      if (!button.dataset.titleActionLabel) {
        button.dataset.titleActionLabel = button.textContent.trim() || fallback;
      }
      return button.dataset.titleActionLabel || fallback;
    };

    const setButtonBusy = (button, busy, label = "") => {
      if (!button) return;
      const original = originalButtonLabel(button);
      button.disabled = busy;
      button.setAttribute("aria-busy", String(busy));
      button.textContent = label || (busy ? original : original);
    };

    const restoreButtonSoon = (button, label, delay = 1800) => {
      if (!button) return;
      const original = originalButtonLabel(button);
      button.disabled = false;
      button.setAttribute("aria-busy", "false");
      if (label) button.textContent = label;
      window.setTimeout(() => {
        if (!button.isConnected) return;
        button.disabled = false;
        button.setAttribute("aria-busy", "false");
        button.textContent = original;
      }, delay);
    };

    const createPoller = (step, interval = 900) => {
      let active = false;
      let timer = 0;

      const stop = () => {
        active = false;
        window.clearTimeout(timer);
        timer = 0;
      };

      const tick = async () => {
        if (!active) return;
        let finished = false;
        try {
          finished = Boolean(await step());
        } catch (_error) {
          // Background work remains authoritative. A transient state read is retried.
        }
        if (!active || finished) {
          stop();
          return;
        }
        const delay = document.hidden ? Math.max(1800, interval * 2) : interval;
        timer = window.setTimeout(tick, delay);
      };

      const poller = {
        start() {
          if (active) return;
          active = true;
          tick();
        },
        stop,
        get active() { return active; },
      };
      pollers.add(poller);
      return poller;
    };

    window.addEventListener("pagehide", () => pollers.forEach((poller) => poller.stop()));

    /* ------------------------------------------------------------------------ */
    /* Hero actions and shared title-detail component placement                 */
    /* ------------------------------------------------------------------------ */

    const menuSelector = [
      ".workspace-detail-title-actions .item-action-menu",
      ".movie-detail-menu",
      ".dossier-on-disk > .panel-head .series-controls > .series-menu.item-action-menu",
    ].join(", ");

    const titleMenu = (root = dossier) => root?.querySelector(menuSelector);

    const workflowPath = (pathname) => (
      new RegExp(`^/titles/${titleId}/(?:cover|collections|tvdb)/?$`).test(pathname)
      || /^\/files\/\d+\/rename-movie\/?$/.test(pathname)
    );

    const workflowKindFor = (pathname) => {
      if (pathname.endsWith("/cover")) return "cover";
      if (pathname.endsWith("/collections")) return "collections";
      if (pathname.endsWith("/tvdb")) return "match";
      if (pathname.includes("/rename-movie")) return "rename";
      return "workflow";
    };

    const workflowLabel = (kind) => ({
      cover: "Change cover",
      collections: "Collections",
      match: "Fix match",
      rename: "Preview rename",
      workflow: "Title workflow",
    })[kind] || "Title workflow";

    const cleanMenuSeparators = (popover) => {
      if (!popover) return;
      [...popover.querySelectorAll(":scope > hr")].forEach((rule) => {
        const previous = rule.previousElementSibling;
        const next = rule.nextElementSibling;
        if (!previous || !next || previous.tagName === "HR" || next.tagName === "HR") {
          rule.remove();
        }
      });
    };

    const ensureHeroTitleMenu = () => {
      const detailCopy = dossier.querySelector(".detail-page-head .detail-copy");
      if (!detailCopy) return null;
      let host = detailCopy.querySelector(".workspace-detail-title-actions");
      const menu = host?.querySelector(".item-action-menu") || titleMenu();
      if (!menu) return null;

      if (!host) {
        host = document.createElement("div");
        host.className = "workspace-detail-title-actions";
        host.setAttribute("aria-label", "Title actions");
        detailCopy.append(host);
      }
      menu.open = false;
      menu.classList.add("workspace-title-action-menu");
      if (!host.contains(menu)) host.append(menu);
      return menu;
    };

    const decorateTitleActions = () => {
      const detailCopy = dossier.querySelector(".detail-page-head .detail-copy");
      const posterColumn = dossier.querySelector(".detail-poster-column");
      const menu = ensureHeroTitleMenu();
      if (!detailCopy || !menu) return false;

      const isMovie = menu.classList.contains("movie-detail-menu");
      dossier.classList.toggle("detail-kind-movie", isMovie);
      dossier.classList.toggle("detail-kind-tv", !isMovie);

      let quick = detailCopy.querySelector(".title-quick-actions");
      if (!quick) {
        quick = document.createElement("div");
        quick.className = "title-quick-actions";
        quick.setAttribute("aria-label", "Quick title actions");
        detailCopy.append(quick);
      }

      const popover = menu.querySelector(".series-menu-popover") || menu.querySelector(":scope > div");
      const favoriteForm = popover?.querySelector(`form[action="/titles/${titleId}/favorite"]`);
      if (favoriteForm && !quick.contains(favoriteForm)) quick.append(favoriteForm);

      detailCopy.querySelector(".hero-organization form .favorite-summary")?.closest("form")?.remove();

      const collectionLink = popover?.querySelector(`a[href="/titles/${titleId}/collections"]`);
      if (collectionLink && !quick.contains(collectionLink)) {
        collectionLink.classList.add("title-quick-action");
        collectionLink.dataset.titleWorkflow = "";
        collectionLink.textContent = "+ Collection";
        quick.append(collectionLink);
      }

      const coverLink = popover?.querySelector(`a[href="/titles/${titleId}/cover"]`);
      if (coverLink && posterColumn && !posterColumn.querySelector(".detail-cover-action")) {
        coverLink.className = "detail-cover-action";
        coverLink.dataset.titleWorkflow = "";
        coverLink.textContent = "Change cover";
        posterColumn.append(coverLink);
      }

      popover?.querySelectorAll("a").forEach((link) => {
        try {
          const url = new URL(link.href, window.location.href);
          if (url.origin === window.location.origin && workflowPath(url.pathname)) {
            link.dataset.titleWorkflow = "";
          }
        } catch (_error) {}
      });

      cleanMenuSeparators(popover);
      return true;
    };

    const wireAsideControls = () => {
      const more = dossier.querySelector("#credit-more");
      const extra = dossier.querySelector("#additional-cast");
      if (more && extra && !more.dataset.detailWired) {
        more.dataset.detailWired = "1";
        more.addEventListener("click", () => {
          const opening = extra.hidden;
          extra.hidden = !opening;
          more.setAttribute("aria-expanded", String(opening));
          more.textContent = opening ? "See less" : "See more";
        });
      }

      const overview = dossier.querySelector("#title-overview");
      const overviewMore = dossier.querySelector("#overview-more");
      const overviewDialog = dossier.querySelector("#overview-dialog");
      if (overview && overviewMore) {
        requestAnimationFrame(() => {
          overviewMore.hidden = overview.scrollHeight <= overview.clientHeight + 2;
        });
      }
      if (overviewMore && overviewDialog && !overviewMore.dataset.detailWired) {
        overviewMore.dataset.detailWired = "1";
        overviewMore.addEventListener("click", () => overviewDialog.showModal?.());
        overviewDialog.querySelectorAll("[data-overview-close]").forEach((button) => {
          button.addEventListener("click", () => overviewDialog.close());
        });
      }
    };

    const installFreshMenu = (freshDossier, head) => {
      const freshMenu = titleMenu(freshDossier);
      const copy = head?.querySelector(".detail-copy");
      if (!freshMenu || !copy) return;
      const host = document.createElement("div");
      host.className = "workspace-detail-title-actions";
      host.setAttribute("aria-label", "Title actions");
      const importedMenu = document.importNode(freshMenu, true);
      importedMenu.open = false;
      importedMenu.classList.add("workspace-title-action-menu");
      host.append(importedMenu);
      copy.append(host);
    };

    const patchDetailFromDocument = (parsed, {hero = true, onDisk = false} = {}) => {
      const freshDossier = parsed.querySelector(".media-dossier");
      if (!freshDossier) return false;

      if (hero) {
        const oldHead = dossier.querySelector(".detail-page-head");
        const freshHead = freshDossier.querySelector(".detail-page-head");
        if (oldHead && freshHead) {
          const importedHead = document.importNode(freshHead, true);
          installFreshMenu(freshDossier, importedHead);
          oldHead.replaceWith(importedHead);
        }
      }

      if (onDisk) {
        const oldOnDisk = dossier.querySelector(".dossier-on-disk");
        const freshOnDisk = freshDossier.querySelector(".dossier-on-disk");
        if (oldOnDisk && freshOnDisk) {
          const importedOnDisk = document.importNode(freshOnDisk, true);
          importedOnDisk.querySelector(".movie-detail-menu")?.remove();
          importedOnDisk.querySelector(
            ":scope > .panel-head .series-controls > .series-menu.item-action-menu",
          )?.remove();
          oldOnDisk.replaceWith(importedOnDisk);
        }
      }

      decorateTitleActions();
      wireAsideControls();
      window.dispatchEvent(new CustomEvent("infomancer:title-detail-updated", {
        detail: {titleId, hero, onDisk},
      }));
      return true;
    };

    const refreshDetail = async (options = {}) => {
      const {document: parsed} = await fetchDocument(
        window.location.pathname + window.location.search,
        {headers: {"X-InfoMancer-Hot-Refresh": "1"}},
      );
      if (!patchDetailFromDocument(parsed, options)) throw new Error("Detail fragment missing");
    };

    decorateTitleActions();
    wireAsideControls();

    /* ------------------------------------------------------------------------ */
    /* Favorite                                                                 */
    /* ------------------------------------------------------------------------ */

    document.addEventListener("submit", async (event) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      const form = event.target.closest(`.title-quick-actions form[action="/titles/${titleId}/favorite"]`);
      if (!form) return;
      event.preventDefault();
      const button = form.querySelector("button");
      const wasFavorite = Boolean(button?.classList.contains("active"));
      setButtonBusy(button, true, wasFavorite ? "Removing…" : "Adding…");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          credentials: "same-origin",
          body: new FormData(form),
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const favorite = !wasFavorite;
        if (button) {
          button.classList.toggle("active", favorite);
          button.innerHTML = `<span>★</span>${favorite ? "Remove favorite" : "Add favorite"}`;
          button.dataset.titleActionLabel = button.textContent.trim();
        }
        showToast(favorite ? "Added to Favorites." : "Removed from Favorites.");
      } catch (_error) {
        showToast("Favorite could not be updated.", "error");
      } finally {
        if (button) {
          button.disabled = false;
          button.setAttribute("aria-busy", "false");
        }
      }
    });

    /* ------------------------------------------------------------------------ */
    /* Media inspection                                                         */
    /* ------------------------------------------------------------------------ */

    const mediaStateUrl = `/api/titles/${encodeURIComponent(titleId)}/media-info-state`;

    const ensureTechnicalRail = () => {
      let rail = dossier.querySelector(".detail-technical-rail");
      if (rail) return rail;
      rail = document.createElement("section");
      rail.className = "detail-technical-rail";
      rail.setAttribute("aria-label", "Technical metadata");
      rail.append(document.createElement("dl"));
      const before = dossier.querySelector(".credits-updating, .dossier-on-disk");
      if (before) dossier.insertBefore(rail, before);
      else dossier.append(rail);
      return rail;
    };

    const renderMediaFacts = (snapshot, flash = false) => {
      const facts = snapshot?.facts || [];
      if (!facts.length) return;
      const rail = ensureTechnicalRail();
      rail.classList.add("media-live-facts");
      rail.setAttribute("aria-live", "polite");
      const list = rail.querySelector("dl") || rail.appendChild(document.createElement("dl"));
      list.replaceChildren();
      facts.forEach((fact) => {
        const card = document.createElement("div");
        if (fact.tone) card.classList.add(`media-fact-${fact.tone}`);
        const term = document.createElement("dt");
        term.textContent = fact.label;
        const value = document.createElement("dd");
        value.textContent = fact.value;
        card.append(term, value);
        list.append(card);
      });
      if (flash) {
        rail.classList.remove("media-facts-updated");
        void rail.offsetWidth;
        rail.classList.add("media-facts-updated");
        window.setTimeout(() => rail.classList.remove("media-facts-updated"), 760);
      }
    };

    const renderMediaFiles = (snapshot) => {
      const fileViews = snapshot?.files || [];
      const rows = [...dossier.querySelectorAll(".dossier-on-disk .file-list > article[data-episode-row]")];
      fileViews.forEach((file, index) => {
        const row = rows[index];
        if (!row) return;
        const grow = row.querySelector(":scope > .grow") || row.querySelector(".grow");
        if (!grow) return;

        if (snapshot.kind === "movie") {
          const summary = grow.querySelector(".file-summary");
          if (summary) summary.textContent = file.summary;
          const path = grow.querySelector(".file-path");
          if (path) path.textContent = file.path;
        } else {
          const detail = grow.querySelector("small:not(.media-info-error)");
          if (detail) detail.textContent = file.detail;
        }

        let error = grow.querySelector(".media-info-error");
        if (file.media_info_error) {
          if (!error) {
            error = document.createElement("small");
            error.className = "media-info-error";
            grow.append(error);
          }
          error.textContent = `Media details unavailable: ${file.media_info_error}`;
        } else {
          error?.remove();
        }
      });
    };

    const renderMediaSnapshot = (snapshot, flash = false) => {
      if (!snapshot) return;
      renderMediaFacts(snapshot, flash);
      renderMediaFiles(snapshot);
      window.dispatchEvent(new CustomEvent("infomancer:title-media-updated", {
        detail: {titleId, snapshot},
      }));
    };

    let mediaButton = null;
    const mediaPoller = createPoller(async () => {
      const data = await fetchJson(`${mediaStateUrl}?snapshot=0`);
      const status = data.task?.status || "idle";
      if (!terminalStatuses.has(status)) return false;

      const finalState = await fetchJson(mediaStateUrl);
      renderMediaSnapshot(finalState.snapshot, true);
      if (mediaButton) {
        setButtonBusy(mediaButton, false);
        if (status === "complete") restoreButtonSoon(mediaButton, "Media updated ✓", 1400);
      }
      if (status === "complete") {
        showToast("Media information updated.");
      } else {
        showToast(data.task?.error || "Media inspection stopped.", "error");
      }
      mediaButton = null;
      return true;
    });

    document.addEventListener("submit", async (event) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      const form = event.target.closest(`form[action="/titles/${titleId}/media-info"]`);
      if (!form || workflowBody?.contains(form)) return;
      event.preventDefault();
      form.closest("details")?.removeAttribute("open");
      if (mediaPoller.active) return;

      const button = form.querySelector('button[type="submit"], button:not([type])');
      mediaButton = button;
      setButtonBusy(button, true, "Checking media…");
      try {
        const data = await fetchJson(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: {"X-InfoMancer-Async": "1"},
        });
        if (data.up_to_date) {
          restoreButtonSoon(button, "Media up to date ✓");
          showToast(data.detail || "Media information is up to date.");
          mediaButton = null;
          return;
        }
        if (!data.started) throw new Error(data.detail || "Media inspection could not start.");
        setButtonBusy(button, true, "Inspecting media…");
        mediaPoller.start();
      } catch (error) {
        restoreButtonSoon(button, "Could not start");
        showToast(error.message || "Media inspection could not start.", "error");
        mediaButton = null;
      }
    });

    fetchJson(mediaStateUrl)
      .then((data) => renderMediaSnapshot(data.snapshot, false))
      .catch(() => {});

    /* ------------------------------------------------------------------------ */
    /* Metadata refresh                                                         */
    /* ------------------------------------------------------------------------ */

    const metadataStateUrl = `/api/titles/${encodeURIComponent(titleId)}/metadata-refresh-state`;
    let metadataButton = null;

    const backgroundStatus = (data) => {
      const queueStatus = data.queue?.status || "";
      const taskStatus = data.task?.status || "";
      if (runningStatuses.has(queueStatus)) return queueStatus;
      if (runningStatuses.has(taskStatus)) return taskStatus;
      if (terminalStatuses.has(queueStatus)) return queueStatus;
      return taskStatus || queueStatus || "idle";
    };

    const metadataPoller = createPoller(async () => {
      const data = await fetchJson(metadataStateUrl);
      const status = backgroundStatus(data);
      if (!terminalStatuses.has(status)) return false;

      if (metadataButton) setButtonBusy(metadataButton, false);
      if (status === "complete") {
        await refreshDetail({hero: true});
        showToast("Metadata refreshed.");
      } else {
        showToast(
          data.task?.error || data.queue?.error || data.metadata_refresh_error || "Metadata refresh stopped.",
          "error",
        );
      }
      metadataButton = null;
      return true;
    });

    document.addEventListener("submit", async (event) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      const form = event.target.closest(`form[action="/titles/${titleId}/imdb-refresh"]`);
      if (!form || workflowBody?.contains(form)) return;
      event.preventDefault();
      form.closest("details")?.removeAttribute("open");
      if (metadataPoller.active) return;

      const button = form.querySelector('button[type="submit"], button:not([type])');
      metadataButton = button;
      setButtonBusy(button, true, "Starting refresh…");
      try {
        const data = await fetchJson(form.action, {
          method: "POST",
          body: new FormData(form),
          headers: {"X-InfoMancer-Async": "1"},
        });
        if (!data.started) throw new Error(data.detail || "Metadata refresh could not start.");
        setButtonBusy(button, true, "Refreshing metadata…");
        metadataPoller.start();
      } catch (error) {
        restoreButtonSoon(button, "Could not start");
        showToast(error.message || "Metadata refresh could not start.", "error");
        metadataButton = null;
      }
    });

    /* ------------------------------------------------------------------------ */
    /* Generic title workflow overlay                                           */
    /* ------------------------------------------------------------------------ */

    const resetWorkflowState = () => {
      workflowDialog?.classList.remove("title-workflow-dialog", "loading");
      workflowDialog?.removeAttribute("aria-busy");
      workflowBody?.removeAttribute("aria-busy");
      workflowBody?.replaceChildren();
      workflowKind = "";
      workflowUrl = window.location.href;
    };

    const closeWorkflow = () => {
      const opener = workflowOpener;
      if (workflowDialog?.open) workflowDialog.close();
      resetWorkflowState();
      workflowOpener = null;
      opener?.focus?.();
    };

    workflowDialog?.addEventListener("close", () => {
      if (!workflowDialog.classList.contains("title-workflow-dialog")) return;
      resetWorkflowState();
      workflowOpener = null;
    });

    const normalizeWorkflowContent = () => {
      if (!workflowBody) return;
      workflowBody.querySelectorAll(".back").forEach((node) => node.remove());
      workflowBody.querySelectorAll('input[name="return_to"]').forEach((input) => {
        input.value = `/titles/${titleId}`;
      });
      const heading = workflowBody.querySelector("h1");
      if (heading) {
        heading.id = "organize-dialog-title";
        workflowDialog?.setAttribute("aria-labelledby", heading.id);
      }
      workflowBody.querySelectorAll("a").forEach((link) => {
        const text = link.textContent.trim().toLowerCase();
        if (text !== "cancel") return;
        try {
          const url = new URL(link.href, workflowUrl);
          if (url.origin === window.location.origin && url.pathname === `/titles/${titleId}`) {
            link.dataset.titleWorkflowClose = "";
          }
        } catch (_error) {}
      });
      workflowBody.setAttribute("aria-busy", "false");
    };

    const workflowCompletedMessage = (kind) => ({
      cover: "Cover updated.",
      match: "Match updated.",
      rename: "File information updated.",
      collections: "Collection membership updated.",
      workflow: "Title updated.",
    })[kind] || "Title updated.";

    const renderWorkflowResponse = async (response) => {
      workflowUrl = response.url || workflowUrl;
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const responsePath = new URL(workflowUrl, window.location.href).pathname;

      if (parsed.querySelector(".media-dossier") || responsePath === `/titles/${titleId}`) {
        const completedKind = workflowKind;
        const options = completedKind === "rename"
          ? {hero: false, onDisk: true}
          : {hero: completedKind !== "collections", onDisk: false};
        await refreshDetail(options);
        closeWorkflow();
        showToast(workflowCompletedMessage(completedKind));
        return true;
      }

      const main = parsed.querySelector("main.shell") || parsed.querySelector("main");
      if (!main || !workflowBody) return false;
      workflowBody.replaceChildren(...[...main.childNodes].map((node) => document.importNode(node, true)));
      normalizeWorkflowContent();
      workflowDialog?.classList.remove("loading");
      workflowDialog?.setAttribute("aria-busy", "false");
      workflowBody.scrollTop = 0;
      return true;
    };

    const openWorkflow = async (url, trigger = null) => {
      if (!workflowDialog || !workflowBody || typeof workflowDialog.showModal !== "function") {
        window.location.assign(url);
        return;
      }
      const parsedUrl = new URL(url, window.location.href);
      workflowKind = workflowKindFor(parsedUrl.pathname);
      workflowUrl = parsedUrl.href;
      workflowOpener = trigger;
      workflowDialog.classList.add("title-workflow-dialog", "loading");
      workflowDialog.setAttribute("aria-busy", "true");
      workflowDialog.setAttribute("aria-label", workflowLabel(workflowKind));
      workflowBody.setAttribute("aria-busy", "true");
      workflowBody.innerHTML = `<div class="title-workflow-loading">Loading ${workflowLabel(workflowKind).toLowerCase()}…</div>`;
      if (!workflowDialog.open) workflowDialog.showModal();

      try {
        const response = await fetch(parsedUrl.href, {
          credentials: "same-origin",
          cache: "no-store",
          headers: {"X-Requested-With": "InfoMancerDialog"},
        });
        if (!response.ok || !(await renderWorkflowResponse(response))) {
          throw new Error(`HTTP ${response.status}`);
        }
      } catch (_error) {
        closeWorkflow();
        window.location.assign(parsedUrl.href);
      }
    };

    document.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const close = event.target.closest("[data-title-workflow-close]");
      if (close && workflowDialog?.contains(close)) {
        event.preventDefault();
        closeWorkflow();
        return;
      }

      const link = event.target.closest("a");
      if (!link || event.defaultPrevented || event.button !== 0
          || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!dossier.contains(link) && !workflowBody?.contains(link)) return;

      let url;
      try {
        const base = workflowBody?.contains(link) ? workflowUrl : window.location.href;
        url = new URL(link.getAttribute("href") || link.href, base);
      } catch (_error) {
        return;
      }
      if (url.origin !== window.location.origin || !workflowPath(url.pathname)) return;
      event.preventDefault();
      link.closest("details")?.removeAttribute("open");
      openWorkflow(url.href, link);
    }, true);

    workflowBody?.addEventListener("submit", async (event) => {
      if (!(event.target instanceof HTMLFormElement)) return;
      const form = event.target;
      event.preventDefault();
      const submitter = event.submitter;
      submitter?.setAttribute("disabled", "");
      workflowDialog?.classList.add("loading");
      workflowDialog?.setAttribute("aria-busy", "true");
      workflowBody.setAttribute("aria-busy", "true");

      try {
        const method = (form.method || "get").toUpperCase();
        const rawAction = form.getAttribute("action") || workflowUrl;
        const action = new URL(rawAction, workflowUrl);
        let response;
        if (method === "GET") {
          const query = new URLSearchParams(new FormData(form));
          action.search = query.toString();
          response = await fetch(action.href, {
            credentials: "same-origin",
            cache: "no-store",
            headers: {"X-Requested-With": "InfoMancerDialog"},
          });
        } else {
          response = await fetch(action.href, {
            method,
            credentials: "same-origin",
            body: new FormData(form),
            headers: {"X-Requested-With": "InfoMancerDialog"},
          });
        }
        if (!response.ok || !(await renderWorkflowResponse(response))) {
          throw new Error(`HTTP ${response.status}`);
        }
      } catch (_error) {
        const fallback = (() => {
          try {
            return new URL(form.getAttribute("action") || workflowUrl, workflowUrl).href;
          } catch (_inner) {
            return workflowUrl;
          }
        })();
        showToast("That workflow could not finish in the overlay. Opening the full page instead.", "error");
        closeWorkflow();
        window.location.assign(fallback);
      } finally {
        submitter?.removeAttribute("disabled");
      }
    });
  });
})();
