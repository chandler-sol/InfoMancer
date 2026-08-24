(() => {
  const body = document.body;
  if (!body) return;

  let versionQuery = '';
  try {
    versionQuery = new URL(document.currentScript?.src || '', window.location.href).search;
  } catch (_error) {}

  /* record_search is a one-shot server marker used by a committed global search.
     The server has already persisted that search before this page renders. Remove
     it immediately so live Library filtering, view hydration, and other partial
     requests cannot replay the same marker for every subsequent keypress. */
  try {
    const currentUrl = new URL(window.location.href);
    if (currentUrl.searchParams.has('record_search')) {
      currentUrl.searchParams.delete('record_search');
      window.history.replaceState(
        window.history.state,
        '',
        currentUrl.pathname + currentUrl.search + currentUrl.hash,
      );
    }
  } catch (_error) {}

  /* Modal close controls are shared shell chrome. Load one versioned owner for
     every native dialog instead of letting individual fetched modal bodies patch
     their own X glyphs. */
  const dialogControlsStylesheet = document.createElement('link');
  dialogControlsStylesheet.rel = 'stylesheet';
  dialogControlsStylesheet.href = `/static/dialog-controls.css${versionQuery}`;
  document.head.append(dialogControlsStylesheet);

  /* Library control polish was split into its own stylesheet during the 0.8 cleanup.
     Load it explicitly on every catalog surface. Keeping the version query attached
     makes the file participate in the same deployment cache-busting contract as the
     rest of the shell assets. */
  if (['/library', '/movies', '/shows'].includes(window.location.pathname)) {
    const libraryControlsStylesheet = document.createElement('link');
    libraryControlsStylesheet.rel = 'stylesheet';
    libraryControlsStylesheet.href = `/static/library-controls.css${versionQuery}`;
    document.head.append(libraryControlsStylesheet);
  }

  /* Consolidated mobile chrome (general polish, header, detail sections in cascade
     order) is loaded from bootstrap so structural mobile fixes are present before
     the Settings and task controllers perform their late handoff. One request
     replaces the previous three-stylesheet waterfall. */
  const mobileStylesheet = document.createElement('link');
  mobileStylesheet.rel = 'stylesheet';
  mobileStylesheet.href = `/static/mobile.css${versionQuery}`;
  document.head.append(mobileStylesheet);

  const polishController = document.createElement('script');
  polishController.src = `/static/final-mobile-polish.js${versionQuery}`;
  polishController.async = false;
  document.head.append(polishController);

  /* Page-specific Settings features get their own owners. Bootstrap only marks the
     page and loads their version-matched assets early enough to avoid a first-paint
     flash of the legacy fallback surface. */
  if (window.location.pathname === '/settings/metadata') {
    body.classList.add('metadata-maintenance-enhanced');

    const stylesheet = document.createElement('link');
    stylesheet.rel = 'stylesheet';
    stylesheet.href = `/static/metadata-maintenance.css${versionQuery}`;
    document.head.append(stylesheet);

    document.addEventListener('DOMContentLoaded', () => {
      const controller = document.createElement('script');
      controller.src = `/static/metadata-maintenance.js${versionQuery}`;
      controller.async = true;
      document.head.append(controller);
    }, {once: true});
  }

  if (!body.classList.contains('has-app-sidebar')) return;
  try {
    const stored = Number.parseInt(localStorage.getItem('infomancer-sidebar-width') || '258', 10);
    const width = Math.min(380, Math.max(220, Number.isFinite(stored) ? stored : 258));
    document.documentElement.style.setProperty('--app-sidebar-width', `${width}px`);
    if (localStorage.getItem('infomancer-sidebar-collapsed') === '1') {
      body.classList.add('sidebar-collapsed');
    }
  } catch (_error) {}
})();
