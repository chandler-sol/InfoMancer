(() => {
  // Keep the established Workspace runtime intact while allowing small alpha UI
  // enhancements to stay isolated and easy to audit. workspace-core.js is the
  // previous workspace.js blob and is loaded with the same cache-busting query.
  const loaderScript = document.currentScript;
  let assetQuery = "";
  if (loaderScript?.src) {
    try {
      assetQuery = new URL(loaderScript.src, window.location.href).search || "";
    } catch (_error) {}
  }

  const core = document.createElement("script");
  core.src = `/static/workspace-core.js${assetQuery}`;
  core.async = false;
  document.head.append(core);

  const ensureDetailActionStyles = () => {
    if (document.querySelector("style[data-workspace-title-actions]")) return;
    const style = document.createElement("style");
    style.dataset.workspaceTitleActions = "1";
    style.textContent = `
      .media-dossier .detail-copy {
        position: relative;
        padding-inline-end: 58px;
      }
      .workspace-detail-title-actions {
        position: absolute;
        top: 7px;
        right: 0;
        z-index: 15;
      }
      .workspace-detail-title-actions .series-menu {
        position: relative;
        margin: 0;
      }
      .workspace-detail-title-actions .series-menu-popover {
        left: auto;
        right: 0;
      }

      /* Media facts are compact cards rather than a full-width strip. This keeps
         Source useful before an inspection without making a single value look like
         a page section of its own. */
      .media-dossier .detail-technical-rail.media-live-facts dl {
        display: grid !important;
        grid-template-columns: repeat(auto-fill, minmax(150px, 220px));
        align-items: stretch;
        gap: 8px;
        overflow: visible;
      }
      .media-dossier .detail-technical-rail.media-live-facts dl > div,
      .media-dossier .detail-technical-rail.media-live-facts dl > div:last-child {
        display: grid;
        align-content: center;
        gap: 3px;
        width: auto;
        min-width: 0;
        min-height: 58px;
        padding: 10px 13px;
        border: 1px solid #273542;
        border-radius: var(--im-radius-sm);
        background: rgba(16, 22, 29, .72);
      }
      .media-dossier .detail-technical-rail.media-live-facts .media-fact-muted dd {
        color: var(--muted);
      }
      .media-dossier .detail-technical-rail.media-live-facts .media-fact-warning dd {
        color: #f5c451;
      }
      .media-dossier .detail-technical-rail.media-live-facts.media-facts-updated dl > div {
        animation: media-fact-refresh 720ms ease-out;
      }
      @keyframes media-fact-refresh {
        0% { border-color: rgba(185, 245, 66, .78); background: rgba(185, 245, 66, .11); }
        100% { border-color: #273542; background: rgba(16, 22, 29, .72); }
      }

      @media (max-width: 760px) {
        .media-dossier .detail-copy {
          padding-inline-end: 52px;
        }
        .workspace-detail-title-actions {
          top: 2px;
        }
        .media-dossier .detail-technical-rail.media-live-facts dl {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 420px) {
        .media-dossier .detail-technical-rail.media-live-facts dl {
          grid-template-columns: 1fr;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .media-dossier .detail-technical-rail.media-live-facts.media-facts-updated dl > div {
          animation: none;
        }
      }
    `;
    document.head.append(style);
  };

  const enhanceDetailTitleActions = () => {
    const dossier = document.querySelector(".media-dossier");
    const detailCopy = dossier?.querySelector(".detail-page-head .detail-copy");
    if (!dossier || !detailCopy || detailCopy.querySelector(".workspace-detail-title-actions")) return;

    // Movie actions currently live beside the first on-disk file. Series actions
    // currently live in the On Disk header. Both are title-level controls, so move
    // the existing menu node into the title hero without cloning or changing forms.
    const movieMenu = dossier.querySelector(".movie-detail-menu");
    const seriesMenu = dossier.querySelector(
      ".dossier-on-disk > .panel-head .series-controls > .series-menu.item-action-menu"
    );
    const menu = movieMenu || seriesMenu;
    if (!menu) return;

    ensureDetailActionStyles();
    menu.open = false;
    menu.classList.add("workspace-title-action-menu");

    const host = document.createElement("div");
    host.className = "workspace-detail-title-actions";
    host.setAttribute("aria-label", "Title actions");
    detailCopy.append(host);
    host.append(menu);
  };

  const enhanceAsyncMediaInspection = () => {
    const dossier = document.querySelector(".media-dossier");
    if (!dossier) return;
    const forms = [...dossier.querySelectorAll('form[action*="/titles/"][action$="/media-info"]')];
    const action = forms[0]?.getAttribute("action") || "";
    const match = action.match(/\/titles\/(\d+)\/media-info$/);
    if (!forms.length || !match) return;

    ensureDetailActionStyles();
    const titleId = match[1];
    const stateUrl = `/api/titles/${encodeURIComponent(titleId)}/media-info-state`;
    let pollTimer = 0;
    let polling = false;

    const buttons = () => forms
      .map((form) => form.querySelector('button[type="submit"], button:not([type])'))
      .filter(Boolean);

    const setButtonsRunning = (running, temporaryLabel = "") => {
      buttons().forEach((button) => {
        if (!button.dataset.mediaInfoLabel) button.dataset.mediaInfoLabel = button.textContent.trim();
        button.disabled = running;
        button.textContent = temporaryLabel || (running ? "Inspecting media…" : button.dataset.mediaInfoLabel);
      });
    };

    const restoreButtonLabelsSoon = (label) => {
      setButtonsRunning(false, label);
      window.setTimeout(() => setButtonsRunning(false), 1800);
    };

    const technicalRail = () => dossier.querySelector(".detail-technical-rail");

    const ensureTechnicalRail = () => {
      let rail = technicalRail();
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

    const renderFacts = (snapshot, flash = false) => {
      const facts = snapshot?.facts || [];
      if (!facts.length) return;
      const rail = ensureTechnicalRail();
      rail.classList.add("media-live-facts");
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

    const renderFiles = (snapshot) => {
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

    const renderSnapshot = (snapshot, flash = false) => {
      if (!snapshot) return;
      renderFacts(snapshot, flash);
      renderFiles(snapshot);
    };

    const fetchState = async () => {
      const response = await fetch(stateUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: {"Accept": "application/json"},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    };

    const stopPolling = () => {
      polling = false;
      window.clearTimeout(pollTimer);
      pollTimer = 0;
    };

    const pollUntilFinished = async () => {
      if (!polling) return;
      try {
        const data = await fetchState();
        const status = data.task?.status || "idle";
        if (["complete", "error", "failed", "cancelled"].includes(status)) {
          stopPolling();
          renderSnapshot(data.snapshot, true);
          setButtonsRunning(false);
          if (status === "error" || status === "failed") {
            restoreButtonLabelsSoon("Inspection stopped");
          }
          return;
        }
        if (status === "starting" || status === "running") {
          setButtonsRunning(true);
        }
      } catch (_error) {
        // A transient polling failure should not make a background inspection fail.
        // The task widget remains the source of truth while we retry the hot refresh.
      }
      pollTimer = window.setTimeout(pollUntilFinished, 900);
    };

    forms.forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        form.closest("details")?.removeAttribute("open");
        if (polling) return;
        setButtonsRunning(true, "Starting inspection…");
        try {
          const response = await fetch(form.action, {
            method: "POST",
            credentials: "same-origin",
            body: new FormData(form),
            headers: {
              "Accept": "application/json",
              "X-InfoMancer-Async": "1",
            },
          });
          let data = null;
          try { data = await response.json(); } catch (_error) {}
          if (!response.ok || !data?.started) {
            throw new Error(data?.detail || `HTTP ${response.status}`);
          }
          polling = true;
          setButtonsRunning(true);
          pollUntilFinished();
        } catch (error) {
          console.warn("Media inspection could not start", error);
          restoreButtonLabelsSoon("Could not start");
        }
      });
    });

    // Normalize the pre-inspection Source-only rail as soon as the detail page is
    // ready. This also means media information completed in another tab appears
    // without requiring a manual refresh here.
    fetchState()
      .then((data) => renderSnapshot(data.snapshot, false))
      .catch(() => {});
  };

  const initializeDetailActions = () => {
    enhanceDetailTitleActions();
    enhanceAsyncMediaInspection();
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeDetailActions, {once: true});
  } else {
    initializeDetailActions();
  }
})();
