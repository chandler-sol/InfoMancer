(() => {
  const librarySurface = document.querySelector('.library-table, #cover-library');
  if (!librarySurface) return;

  const inspector = () => document.getElementById('workspace-inspector');

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
    if (panel) panel.hidden = true;
  };

  window.addEventListener('pagehide', dismissForNavigation);

  /* Chromium/Firefox can restore Library from the back-forward cache without
     re-running its scripts. Enforce the same clean entry state on that restore. */
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) dismissForNavigation();
  });
})();
