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
      @media (max-width: 760px) {
        .media-dossier .detail-copy {
          padding-inline-end: 52px;
        }
        .workspace-detail-title-actions {
          top: 2px;
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

  const initializeDetailActions = () => enhanceDetailTitleActions();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeDetailActions, {once: true});
  } else {
    initializeDetailActions();
  }
})();
