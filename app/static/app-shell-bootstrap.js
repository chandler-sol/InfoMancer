(() => {
  const body = document.body;
  if (!body) return;

  let versionQuery = '';
  try {
    versionQuery = new URL(document.currentScript?.src || '', window.location.href).search;
  } catch (_error) {}

  /* Final release polish is loaded from bootstrap so structural mobile fixes are
     present before the Settings and task controllers perform their late handoff. */
  const polishStylesheet = document.createElement('link');
  polishStylesheet.rel = 'stylesheet';
  polishStylesheet.href = `/static/final-mobile-polish.css${versionQuery}`;
  document.head.append(polishStylesheet);

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

  if (window.location.pathname === '/sources') {
    document.addEventListener('DOMContentLoaded', () => {
      const controller = document.createElement('script');
      controller.src = `/static/source-bulk-actions.js${versionQuery}`;
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
