(() => {
  const body = document.body;
  if (!body) return;

  let versionQuery = '';
  try {
    versionQuery = new URL(document.currentScript?.src || '', window.location.href).search;
  } catch (_error) {}

  /* Keep page content hidden until parser-discovered markup, deferred controllers,
     and the small set of late 0.8 styles have settled. The application chrome stays
     visible, so navigation feels continuous instead of flashing an intermediate
     layout. */
  body.classList.add('shell-preparing');
  const guard = document.createElement('style');
  guard.textContent = `
    body.shell-preparing .shell { visibility: hidden !important; }
    body.shell-preparing { background: #090d11; }
  `;
  document.head.append(guard);

  const ensureStylesheet = (path) => new Promise((resolve) => {
    const href = `/static/${path}${versionQuery}`;
    let absolute = href;
    try { absolute = new URL(href, window.location.href).href; } catch (_error) {}
    const existing = [...document.querySelectorAll('link[rel="stylesheet"]')]
      .find((link) => link.href === absolute);
    if (existing) {
      if (existing.sheet) resolve(existing);
      else {
        existing.addEventListener('load', () => resolve(existing), {once: true});
        existing.addEventListener('error', () => resolve(existing), {once: true});
      }
      return;
    }
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.addEventListener('load', () => resolve(link), {once: true});
    link.addEventListener('error', () => resolve(link), {once: true});
    document.head.append(link);
  });

  const criticalStyles = [
    ensureStylesheet('mobile.css'),
    ensureStylesheet('task-widget.css'),
    ensureStylesheet('app-navigation.css'),
    ensureStylesheet('action-menu.css'),
    ensureStylesheet('release-081-ui-polish.css'),
  ];

  if (['/library', '/movies', '/shows'].includes(window.location.pathname)) {
    criticalStyles.push(ensureStylesheet('library-controls.css'));
  }
  if (window.location.pathname === '/settings/metadata') {
    body.classList.add('metadata-maintenance-enhanced');
    criticalStyles.push(ensureStylesheet('metadata-maintenance.css'));
  }
  if (window.location.pathname === '/sources') {
    criticalStyles.push(ensureStylesheet('source-health.css'));
  }

  const domReady = new Promise((resolve) => {
    if (document.readyState !== 'loading') resolve();
    else document.addEventListener('DOMContentLoaded', resolve, {once: true});
  });

  Promise.all([domReady, ...criticalStyles]).then(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      body.classList.remove('shell-preparing');
      guard.remove();
    }));
  });

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

  const polishController = document.createElement('script');
  polishController.src = `/static/final-mobile-polish.js${versionQuery}`;
  polishController.async = false;
  document.head.append(polishController);

  const releasePolishController = document.createElement('script');
  releasePolishController.src = `/static/release-081-ui-polish.js${versionQuery}`;
  releasePolishController.defer = true;
  document.head.append(releasePolishController);

  /* Page-specific Settings features get their own owners. Their CSS is already in
     the critical set above; load only the controller after the initial DOM settles. */
  if (window.location.pathname === '/settings/metadata') {
    document.addEventListener('DOMContentLoaded', () => {
      const controller = document.createElement('script');
      controller.src = `/static/metadata-maintenance.js${versionQuery}`;
      controller.async = true;
      document.head.append(controller);
    }, {once: true});
  }

  if (window.location.pathname === '/sources') {
    document.addEventListener('DOMContentLoaded', () => {
      const controller = document.createElement('script');
      controller.src = `/static/source-health.js${versionQuery}`;
      controller.async = true;
      document.head.append(controller);
    }, {once: true});
  }

  /* Bulk Match owns its Apply controller directly from each page template. Keeping
     one canonical loader avoids duplicate document-level submit handlers and keeps
     Apply/progressive lifecycle ownership deterministic across browsers and WebViews. */

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
