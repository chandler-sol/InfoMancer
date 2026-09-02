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
     layout. The footer follows the workspace so it cannot flash at the top of an
     otherwise empty content surface while the next page is preparing. */
  body.classList.add('shell-preparing');
  const guard = document.createElement('style');
  guard.textContent = `
    body.shell-preparing .shell,
    body.shell-preparing > footer { visibility: hidden !important; }
    body.shell-preparing { background: #090d11; }
  `;
  document.head.append(guard);

  const ensureStylesheet = (path, {versioned = false} = {}) => new Promise((resolve) => {
    const base = path.startsWith('/static/') ? path : `/static/${path}`;
    const href = versioned ? base : `${base}${versionQuery}`;
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

  const loadScript = (path, {async = false} = {}) => new Promise((resolve) => {
    const script = document.createElement('script');
    script.src = `/static/${path}${versionQuery}`;
    script.async = async;
    script.addEventListener('load', () => resolve(script), {once: true});
    script.addEventListener('error', () => resolve(script), {once: true});
    document.head.append(script);
  });

  const criticalStyles = [
    ensureStylesheet(`/static/mobile.css${versionQuery}`, {versioned: true}),
    ensureStylesheet('task-widget.css'),
    ensureStylesheet('app-navigation.css'),
    ensureStylesheet('action-menu.css'),
    ensureStylesheet('release-081-ui-polish.css'),
  ];

  const path = window.location.pathname;
  const sourcePage = window.location.pathname === '/sources';
  const librarySurface = ['/library', '/movies', '/shows'].includes(path);

  /* A Library can contain hundreds of cards and its final workspace bounds differ
     dramatically from most other pages. Letting Chromium/WebView2 treat main.shell
     as a named cross-document snapshot makes the whole Library interpolate between
     the old and new bounds, which reads as a grow/shrink animation and can stutter
     while compositing a large cover grid. Keep only the application chrome in the
     View Transition on these routes; the Library workspace will reveal atomically. */
  if (librarySurface) {
    document.documentElement.classList.add('library-surface-route');
    const libraryTransitionGuard = document.createElement('style');
    libraryTransitionGuard.textContent = `
      html.library-surface-route main.shell,
      html.library-surface-route body > footer {
        view-transition-name: none !important;
      }
    `;
    document.head.append(libraryTransitionGuard);
  }

  if (librarySurface) {
    [
      'library-controls.css',
      'library-performance.css',
      'library-density.css',
      'library-selection.css',
      'library-saved-views.css',
      'library-letter-jump.css',
    ].forEach((stylesheet) => criticalStyles.push(ensureStylesheet(stylesheet)));
  }
  if (path === '/collections') {
    criticalStyles.push(ensureStylesheet('release-081-collections.css'));
  }
  if (/^\/collections\/\d+$/.test(path)) {
    criticalStyles.push(ensureStylesheet('collection-detail.css'));
  }
  if (path === '/admin/users') {
    criticalStyles.push(ensureStylesheet('user-management.css'));
  }
  if (path === '/settings/metadata') {
    body.classList.add('metadata-maintenance-enhanced');
    criticalStyles.push(ensureStylesheet('metadata-maintenance.css'));
  }
  if (path === '/settings/system') {
    criticalStyles.push(ensureStylesheet('settings-system-nav.css'));
  }
  if (sourcePage) {
    criticalStyles.push(ensureStylesheet('source-health.css'));
  }

  const domReady = new Promise((resolve) => {
    if (document.readyState !== 'loading') resolve();
    else document.addEventListener('DOMContentLoaded', resolve, {once: true});
  });

  /* The Library density controller performs two geometry changes after parsing: it
     moves the display controls beside the scope tabs and applies the saved cover
     footprint. Keep the already-hidden workspace hidden for those few milliseconds
     so WebView2 never paints the intermediate size. A bounded fallback prevents a
     broken optional controller from leaving the workspace hidden indefinitely. */
  const libraryLayoutReady = librarySurface
    ? domReady.then(() => new Promise((resolve) => {
        let finished = false;
        let timer = 0;
        let observer = null;
        const isReady = () => {
          const density = document.getElementById('cover-size-control');
          const toolbar = document.querySelector('.library-view-toolbar');
          const tabs = document.querySelector('.catalog-tabs');
          return Boolean(density?.classList.contains('library-density-ready'))
            && (!toolbar || !tabs || toolbar.parentElement === tabs);
        };
        const finish = () => {
          if (finished) return;
          finished = true;
          window.clearTimeout(timer);
          observer?.disconnect();
          resolve();
        };
        if (isReady()) {
          finish();
          return;
        }
        observer = new MutationObserver(() => {
          if (isReady()) finish();
        });
        observer.observe(document.body, {
          subtree: true,
          childList: true,
          attributes: true,
          attributeFilter: ['class'],
        });
        timer = window.setTimeout(finish, 1500);
      }))
    : Promise.resolve();

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

  const pageControllersReady = domReady.then(async () => {
    const controllers = [loadScript('release-081-ui-polish.js')];
    if (path === '/settings/metadata') {
      controllers.push(loadScript('metadata-maintenance.js', {async: true}));
    }
    if (sourcePage) {
      controllers.push(loadScript('source-health.js', {async: true}));
    }
    await Promise.all(controllers);
  });

  const shellCriticalReady = Promise.all([domReady, pageControllersReady, ...criticalStyles]);
  Promise.all([shellCriticalReady, libraryLayoutReady]).then(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      body.classList.remove('shell-preparing');
      guard.remove();
    }));
  });

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
