(() => {
  const librarySurface = document.querySelector('.library-table, #cover-library');
  if (!librarySurface) return;

  const inspector = () => document.getElementById('workspace-inspector');
  const interactive = (target) => target?.closest?.(
    'input, button, summary, details, form, select, textarea, .item-action-menu, .cover-select-control'
  );

  /* A fresh title should always enter the Inspector at its chrome/identity area.
     The drawer itself persists between selections, so without an explicit reset a
     mobile user could open the next title at the previous title's scroll offset. */
  document.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || interactive(event.target)) return;
    const item = event.target.closest?.('.cover-card, .library-title-row');
    if (!item) return;
    const panel = inspector();
    if (!panel) return;
    panel.scrollTop = 0;
    panel.scrollLeft = 0;
  }, true);

  /* Inspector state is intentionally visit-local. A selected title may remain
     checked when the user comes back, but the drawer itself should not survive a
     trip to Review, Settings, a title page, or another destination. Clearing the
     history marker before clicking Close prevents the Inspector's normal close
     handler from navigating backward while the page is already leaving. */
  const dismissForNavigation = () => {
    const panel = inspector();
    const open = document.body.classList.contains('workspace-inspector-open');
    const url = new URL(window.location.href);
    const hasInspectorHistory = Boolean(url.searchParams.get('inspect') || history.state?.workspaceInspectorTitleId);
    if (!open && !hasInspectorHistory) return;

    url.searchParams.delete('inspect');
    const state = {...(history.state || {}), workspaceInspectorTitleId: null};
    history.replaceState(state, '', url.pathname + url.search + url.hash);

    if (open) panel?.querySelector('.workspace-inspector-close')?.click();
    document.body.classList.remove('workspace-inspector-open');
    if (panel) {
      panel.hidden = true;
      panel.scrollTop = 0;
      panel.scrollLeft = 0;
    }
  };

  window.addEventListener('pagehide', dismissForNavigation);

  /* Chromium/Firefox can restore Library from the back-forward cache without
     re-running its scripts. Enforce the same clean entry state on that restore. */
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) dismissForNavigation();
  });
})();
