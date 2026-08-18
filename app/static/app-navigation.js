(() => {
  const root = document.documentElement;
  let pendingTimer = 0;

  const clearPending = () => {
    window.clearTimeout(pendingTimer);
    root.classList.remove("app-navigation-pending");
  };

  const sameDocumentHashOnly = (url) => (
    url.pathname === window.location.pathname
    && url.search === window.location.search
    && Boolean(url.hash)
  );

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href]");
    if (!link || link.hasAttribute("download")) return;
    if (link.target && link.target !== "_self") return;

    let url;
    try {
      url = new URL(link.href, window.location.href);
    } catch (_error) {
      return;
    }
    if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol)) return;
    if (sameDocumentHashOnly(url)) return;

    root.classList.add("app-navigation-pending");
    pendingTimer = window.setTimeout(clearPending, 5000);
  });

  window.addEventListener("pageshow", clearPending);
  window.addEventListener("pagehide", () => window.clearTimeout(pendingTimer));
})();
