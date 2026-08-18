(() => {
  const root = document.documentElement;
  let pendingTimer = 0;
  let libraryWarmPromise = null;
  let lastLibraryWarm = 0;

  const clearPending = () => {
    window.clearTimeout(pendingTimer);
    root.classList.remove("app-navigation-pending");
  };

  const sameDocumentHashOnly = (url) => (
    url.pathname === window.location.pathname
    && url.search === window.location.search
    && Boolean(url.hash)
  );

  const canWarmLibrary = () => {
    if (window.location.pathname === "/library") return false;
    if (document.visibilityState !== "visible") return false;
    if (navigator.connection?.saveData) return false;
    return Date.now() - lastLibraryWarm > 2500;
  };

  const warmLibrary = () => {
    if (!canWarmLibrary()) return libraryWarmPromise;
    if (libraryWarmPromise) return libraryWarmPromise;
    lastLibraryWarm = Date.now();
    libraryWarmPromise = fetch("/library", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: {"X-InfoMancer-Prefetch": "library"},
    }).then((response) => response.arrayBuffer())
      .catch(() => null)
      .finally(() => { libraryWarmPromise = null; });
    return libraryWarmPromise;
  };

  document.addEventListener("pointerover", (event) => {
    const link = event.target.closest('a[href="/library"]');
    if (link) warmLibrary();
  }, {passive: true});

  const scheduleIdleLibraryWarm = () => {
    if (!canWarmLibrary()) return;
    const run = () => warmLibrary();
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, {timeout: 1400});
    } else {
      window.setTimeout(run, 900);
    }
  };

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

  if (document.readyState === "complete") scheduleIdleLibraryWarm();
  else window.addEventListener("load", scheduleIdleLibraryWarm, {once: true});

  window.addEventListener("pageshow", clearPending);
  window.addEventListener("pagehide", () => window.clearTimeout(pendingTimer));
})();
