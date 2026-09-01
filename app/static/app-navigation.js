(() => {
  const root = document.documentElement;
  let pendingTimer = 0;
  let pendingDelayTimer = 0;
  let leavingTimer = 0;
  let libraryWarmPromise = null;
  let lastLibraryWarm = 0;

  const clearPending = () => {
    window.clearTimeout(pendingDelayTimer);
    window.clearTimeout(pendingTimer);
    pendingDelayTimer = 0;
    pendingTimer = 0;
    root.classList.remove('app-navigation-pending');
    delete root.dataset.navigationTarget;
  };

  const clearLeaving = () => {
    window.clearTimeout(leavingTimer);
    leavingTimer = 0;
    root.classList.remove('app-navigation-leaving');
  };

  const announceNavigation = (url = null) => {
    const href = url ? String(url) : '';
    if (href) root.dataset.navigationTarget = href;
    document.dispatchEvent(new CustomEvent('infomancer:before-navigate', {
      detail: {url: href},
    }));
  };

  const coverOutgoingPage = () => {
    clearLeaving();
    root.classList.add('app-navigation-leaving');
    /* If a browser or embedded WebView cancels navigation after the click, never
       strand the user behind the transition cover indefinitely. */
    leavingTimer = window.setTimeout(clearLeaving, 5000);
  };

  const showPendingSoon = () => {
    clearPending();
    pendingDelayTimer = window.setTimeout(() => {
      root.classList.add('app-navigation-pending');
      pendingTimer = window.setTimeout(clearPending, 5000);
    }, 120);
  };

  const beginNavigation = (url = null) => {
    announceNavigation(url);
    coverOutgoingPage();
    showPendingSoon();
  };

  const validLibraryView = (value) => ['list', 'covers'].includes(value) ? value : '';
  const libraryViewCookie = () => {
    const prefix = 'infomancer_library_view=';
    const value = document.cookie.split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix))
      ?.slice(prefix.length) || '';
    return validLibraryView(decodeURIComponent(value));
  };

  /* The cookie is the server-visible source of truth, so prefer it when old builds
     left localStorage and the cookie out of sync. localStorage remains a migration
     fallback only when no valid cookie exists yet. */
  const savedLibraryView = (() => {
    const cookieView = libraryViewCookie();
    if (cookieView) {
      try { localStorage.setItem('infomancer-library-view', cookieView); } catch (_error) {}
      return cookieView;
    }
    try {
      const saved = validLibraryView(localStorage.getItem('infomancer-library-view') || '');
      if (saved) {
        document.cookie = `infomancer_library_view=${saved}; Path=/; SameSite=Lax; Max-Age=31536000`;
        return saved;
      }
    } catch (_error) {}
    return '';
  })();

  const sameDocumentHashOnly = (url) => (
    url.pathname === window.location.pathname
    && url.search === window.location.search
    && Boolean(url.hash)
  );

  const canWarmLibrary = () => {
    if (window.location.pathname === '/library') return false;
    if (document.visibilityState !== 'visible') return false;
    if (navigator.connection?.saveData) return false;
    if (['slow-2g', '2g'].includes(navigator.connection?.effectiveType)) return false;
    return Date.now() - lastLibraryWarm > 2500;
  };

  const warmLibrary = () => {
    if (!canWarmLibrary()) return libraryWarmPromise;
    if (libraryWarmPromise) return libraryWarmPromise;
    lastLibraryWarm = Date.now();
    const headers = {'X-InfoMancer-Prefetch': 'library'};
    if (savedLibraryView) headers['X-InfoMancer-Library-View'] = savedLibraryView;
    libraryWarmPromise = fetch('/library', {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers,
    }).then((response) => {
      response.body?.cancel();
      return null;
    }).catch(() => null)
      .finally(() => { libraryWarmPromise = null; });
    return libraryWarmPromise;
  };

  document.addEventListener('pointerover', (event) => {
    const link = event.target.closest('a[href="/library"]');
    if (link) warmLibrary();
  }, {passive: true});

  document.addEventListener('focusin', (event) => {
    const link = event.target.closest?.('a[href="/library"]');
    if (link) warmLibrary();
  });

  const scheduleIdleLibraryWarm = () => {
    if (!canWarmLibrary()) return;
    const run = () => warmLibrary();
    if ('requestIdleCallback' in window) {
      requestIdleCallback(run, {timeout: 1800});
    } else {
      window.setTimeout(run, 1200);
    }
  };

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download')) return;
    if (link.target && link.target !== '_self') return;

    let url;
    try { url = new URL(link.href, window.location.href); }
    catch (_error) { return; }
    if (url.origin !== window.location.origin || !['http:', 'https:'].includes(url.protocol)) return;
    if (sameDocumentHashOnly(url)) return;
    beginNavigation(url.pathname + url.search + url.hash);
  });

  document.addEventListener('submit', (event) => {
    if (event.defaultPrevented) return;
    const form = event.target.closest('form');
    if (!form || form.target && form.target !== '_self') return;
    if (form.matches('[data-workspace-ajax], [data-dialog-form]') || form.closest('dialog[open]')) return;
    const action = new URL(form.action || window.location.href, window.location.href);
    if (action.origin !== window.location.origin) return;
    beginNavigation(action.pathname + action.search + action.hash);
  });

  if (document.readyState === 'complete') scheduleIdleLibraryWarm();
  else window.addEventListener('load', scheduleIdleLibraryWarm, {once: true});

  /* Keep the long-standing cleanup listener intact for bfcache/pageshow behavior,
     then independently remove the 0.8.1 blank transition cover. */
  window.addEventListener('pageshow', clearPending);
  window.addEventListener('pageshow', clearLeaving);
  window.addEventListener('pagehide', () => {
    announceNavigation();
    clearPending();
  });
})();
