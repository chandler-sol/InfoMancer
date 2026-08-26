(() => {
  const back = document.querySelector('[data-first-run-back]');
  if (!back) return;

  // The desktop launcher keeps itself in WebView history while the first
  // Librarian is being created. Ordinary browser/server setup stays unchanged.
  if (window.history.length <= 1) return;

  back.hidden = false;
  back.addEventListener('click', () => window.history.back());
})();
