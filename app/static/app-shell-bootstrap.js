(() => {
  if (!document.body?.classList.contains('has-app-sidebar')) return;
  try {
    const stored = Number.parseInt(localStorage.getItem('infomancer-sidebar-width') || '258', 10);
    const width = Math.min(380, Math.max(220, Number.isFinite(stored) ? stored : 258));
    document.documentElement.style.setProperty('--app-sidebar-width', `${width}px`);
    if (localStorage.getItem('infomancer-sidebar-collapsed') === '1') {
      document.body.classList.add('sidebar-collapsed');
    }
  } catch (_error) {}
})();
