(() => {
  const root = document.documentElement;
  let pendingTimer = 0;
  let pendingDelayTimer = 0;
  let libraryWarmPromise = null;
  let lastLibraryWarm = 0;

  const clearPending = () => {
    window.clearTimeout(pendingDelayTimer);
    window.clearTimeout(pendingTimer);
    pendingDelayTimer = 0;
    pendingTimer = 0;
    root.classList.remove("app-navigation-pending");
    delete root.dataset.navigationTarget;
  };

  const announceNavigation = (url = null) => {
    const href = url ? String(url) : "";
    if (href) root.dataset.navigationTarget = href;
    document.dispatchEvent(new CustomEvent("infomancer:before-navigate", {
      detail: {url: href},
    }));
  };

  const showPendingSoon = () => {
    clearPending();
    /* Do not flash a progress bar for navigations that complete almost instantly.
       The indicator is useful feedback only once the user can actually perceive a wait. */
    pendingDelayTimer = window.setTimeout(() => {
      root.classList.add("app-navigation-pending");
      pendingTimer = window.setTimeout(clearPending, 5000);
    }, 120);
  };

  const beginNavigation = (url = null) => {
    announceNavigation(url);
    showPendingSoon();
  };

  /* base.html owns the global-search interaction, but its delayed focus and recent
     search popover can outlive the visual collapse. Keep the closed state truly
     closed: no invisible focused input and no suggestion panel left hanging under
     the circular search button. */
  const search = document.getElementById("global-search");
  const searchInput = document.getElementById("global-search-input");
  const searchSuggestions = document.getElementById("global-search-suggestions");
  const settleClosedSearch = () => {
    if (!search || search.classList.contains("open")) return;
    if (searchSuggestions) searchSuggestions.hidden = true;
    if (document.activeElement === searchInput) searchInput.blur();
  };
  if (search && searchInput) {
    new MutationObserver(settleClosedSearch).observe(search, {
      attributes: true,
      attributeFilter: ["class"],
    });
    searchInput.addEventListener("focus", () => {
      if (!search.classList.contains("open")) searchInput.blur();
    });
    settleClosedSearch();
  }

  const savedLibraryView = (() => {
    try {
      const saved = window.localStorage.getItem("infomancer-library-view") || "";
      if (["list", "covers"].includes(saved)) {
        document.cookie = `infomancer_library_view=${saved}; Path=/; SameSite=Lax; Max-Age=31536000`;
        return saved;
      }
    } catch (_error) {}
    return "";
  })();

  const sameDocumentHashOnly = (url) => (
    url.pathname === window.location.pathname
    && url.search === window.location.search
    && Boolean(url.hash)
  );

  const canWarmLibrary = () => {
    if (window.location.pathname === "/library") return false;
    if (document.visibilityState !== "visible") return false;
    if (navigator.connection?.saveData) return false;
    if (["slow-2g", "2g"].includes(navigator.connection?.effectiveType)) return false;
    return Date.now() - lastLibraryWarm > 2500;
  };

  const warmLibrary = () => {
    if (!canWarmLibrary()) return libraryWarmPromise;
    if (libraryWarmPromise) return libraryWarmPromise;
    lastLibraryWarm = Date.now();
    const headers = {"X-InfoMancer-Prefetch": "library"};
    if (savedLibraryView) headers["X-InfoMancer-Library-View"] = savedLibraryView;
    libraryWarmPromise = fetch("/library", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers,
    }).then((response) => {
      /* The optimized server answers warm requests with 204 after populating its
         render cache. Cancel any body defensively so an older server or proxy can
         never turn a hover warm-up into a discarded full-Library download. */
      response.body?.cancel();
      return null;
    }).catch(() => null)
      .finally(() => { libraryWarmPromise = null; });
    return libraryWarmPromise;
  };

  document.addEventListener("pointerover", (event) => {
    const link = event.target.closest('a[href="/library"]');
    if (link) warmLibrary();
  }, {passive: true});

  document.addEventListener("focusin", (event) => {
    const link = event.target.closest?.('a[href="/library"]');
    if (link) warmLibrary();
  });

  const scheduleIdleLibraryWarm = () => {
    if (!canWarmLibrary()) return;
    const run = () => warmLibrary();
    if ("requestIdleCallback" in window) {
      window.requestIdleCallback(run, {timeout: 1800});
    } else {
      window.setTimeout(run, 1200);
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

    beginNavigation(url.pathname + url.search + url.hash);
  });

  /* Full-page form navigations should clear transient UI and receive the same
     delayed progress treatment as links. AJAX/dialog handlers register earlier and
     preventDefault before this listener runs, so they remain entirely in-place. */
  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const form = event.target.closest("form");
    if (!form || form.target && form.target !== "_self") return;
    if (form.matches("[data-workspace-ajax], [data-dialog-form]") || form.closest("dialog[open]")) return;
    const action = new URL(form.action || window.location.href, window.location.href);
    if (action.origin !== window.location.origin) return;
    beginNavigation(action.pathname + action.search + action.hash);
  });

  if (document.readyState === "complete") scheduleIdleLibraryWarm();
  else window.addEventListener("load", scheduleIdleLibraryWarm, {once: true});

  window.addEventListener("pageshow", clearPending);
  window.addEventListener("pagehide", () => {
    announceNavigation();
    clearPending();
  });
})();
