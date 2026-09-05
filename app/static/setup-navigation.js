(() => {
  const back = document.querySelector('[data-first-run-back]');
  if (!back) return;

  // The desktop launcher deliberately keeps itself in WebView history while the
  // first Librarian is being created. Reveal this only when there is somewhere
  // meaningful to go back to, so ordinary server/browser setup is unaffected.
  if (window.history.length <= 1) return;

  back.hidden = false;
  back.addEventListener('click', () => window.history.back());
})();
