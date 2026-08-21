(() => {
  const listSurface = document.querySelector('.library-table');
  const coverSurface = document.getElementById('cover-library');
  const listButton = document.getElementById('library-list-view');
  const coverButton = document.getElementById('library-cover-view');
  const densityControl = document.getElementById('cover-size-control');
  if (!listSurface || !coverSurface || !listButton || !coverButton) return;

  const COOKIE_NAME = 'infomancer_library_view';
  const STORAGE_KEY = 'infomancer-library-view';
  const inflight = new Map();

  const validView = (value) => ['list', 'covers'].includes(value) ? value : '';
  const currentView = () => coverSurface.hidden ? 'list' : 'covers';
  const cookieView = () => {
    const prefix = `${COOKIE_NAME}=`;
    const value = document.cookie.split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix))
      ?.slice(prefix.length) || '';
    return validView(decodeURIComponent(value));
  };

  const setViewCookie = (view) => {
    if (!validView(view)) return;
    document.cookie = `${COOKIE_NAME}=${view}; Path=/; SameSite=Lax; Max-Age=31536000`;
  };

  const rememberView = (view) => {
    setViewCookie(view);
    try { localStorage.setItem(STORAGE_KEY, view); } catch (_error) {}
  };

  const markLoading = (view) => {
    if (view === 'covers') {
      if (!coverSurface.querySelector('.library-surface-loading')) {
        const loading = document.createElement('div');
        loading.className = 'library-surface-loading';
        loading.textContent = 'Loading cover view…';
        coverSurface.append(loading);
      }
      return;
    }
    const body = listSurface.querySelector('tbody');
    if (body && !body.children.length) {
      const row = document.createElement('tr');
      row.className = 'library-surface-loading-row';
      row.innerHTML = '<td colspan="7">Loading list view…</td>';
      body.append(row);
    }
  };

  const extractSurface = (html, view) => {
    const marker = view === 'covers'
      ? '<section class="cover-library" id="cover-library"'
      : '<section class="panel table-wrap library-table"';
    const start = html.indexOf(marker);
    if (start < 0) return null;
    const end = html.indexOf('</section>', start);
    if (end < 0) return null;
    const template = document.createElement('template');
    template.innerHTML = html.slice(start, end + '</section>'.length).trim();
    return template.content.firstElementChild;
  };

  const fetchSurface = (view) => {
    if (inflight.has(view)) return inflight.get(view);
    const request = fetch(window.location.pathname + window.location.search, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'X-InfoMancer-Library-View': view,
        'X-InfoMancer-Partial': 'library-surface',
      },
    }).then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return extractSurface(await response.text(), view);
    }).finally(() => inflight.delete(view));
    inflight.set(view, request);
    return request;
  };

  const hydrateSurface = async (view, {announce = true, showLoading = true} = {}) => {
    const surface = view === 'covers' ? coverSurface : listSurface;
    if (surface.dataset.librarySurfacePlaceholder !== view) return true;
    if (surface.dataset.librarySurfaceLoading === '1') return inflight.get(view) || false;

    surface.dataset.librarySurfaceLoading = '1';
    if (showLoading) markLoading(view);
    try {
      const replacement = await fetchSurface(view);
      if (!replacement || replacement.dataset.librarySurfacePlaceholder === view) {
        throw new Error(`${view} surface was not returned`);
      }

      if (view === 'covers') {
        coverSurface.replaceChildren(...replacement.childNodes);
      } else {
        const replacementTable = replacement.querySelector('table');
        const replacementHead = replacementTable?.querySelector('thead');
        const replacementBody = replacementTable?.querySelector('tbody');
        const currentTable = listSurface.querySelector('table');
        const currentBody = currentTable?.querySelector('tbody');
        if (!replacementBody || !currentTable || !currentBody) {
          throw new Error('List surface was not returned');
        }
        const currentHead = currentTable.querySelector('thead');
        if (replacementHead) {
          if (currentHead) currentHead.replaceWith(replacementHead.cloneNode(true));
          else currentTable.prepend(replacementHead.cloneNode(true));
        }
        currentBody.replaceChildren(...replacementBody.childNodes);
        if (replacement.dataset.libraryKind) {
          listSurface.dataset.libraryKind = replacement.dataset.libraryKind;
        }
      }

      delete surface.dataset.librarySurfacePlaceholder;
      if (announce) {
        document.dispatchEvent(new CustomEvent('infomancer:library-results-updated'));
      }
      return true;
    } catch (_error) {
      if (!showLoading) return false;
      if (view === 'covers') {
        coverSurface.replaceChildren();
        const message = document.createElement('div');
        message.className = 'library-surface-error';
        message.textContent = 'Cover view could not be loaded. Try again.';
        coverSurface.append(message);
      } else {
        const body = listSurface.querySelector('tbody');
        if (body) {
          body.innerHTML = '<tr><td colspan="7" class="empty">List view could not be loaded. Try again.</td></tr>';
        }
      }
      return false;
    } finally {
      delete surface.dataset.librarySurfaceLoading;
    }
  };

  const applyView = async (view, {persist = true, hydrate = true} = {}) => {
    const next = validView(view) || 'list';
    const covers = next === 'covers';
    listSurface.hidden = covers;
    coverSurface.hidden = !covers;
    if (densityControl) densityControl.hidden = !covers;
    listButton.classList.toggle('active', !covers);
    coverButton.classList.toggle('active', covers);
    listButton.setAttribute('aria-pressed', String(!covers));
    coverButton.setAttribute('aria-pressed', String(covers));
    if (persist) rememberView(next);
    if (hydrate) await hydrateSurface(next);
    document.dispatchEvent(new CustomEvent('infomancer:library-view-changed', {
      detail: {view: next},
    }));
  };

  listButton.addEventListener('click', () => applyView('list'));
  coverButton.addEventListener('click', () => applyView('covers'));

  const warm = (view) => {
    const surface = view === 'covers' ? coverSurface : listSurface;
    if (surface.dataset.librarySurfacePlaceholder !== view) return;
    hydrateSurface(view, {announce: false, showLoading: false});
  };
  listButton.addEventListener('pointerenter', () => warm('list'), {passive: true});
  coverButton.addEventListener('pointerenter', () => warm('covers'), {passive: true});
  listButton.addEventListener('focus', () => warm('list'));
  coverButton.addEventListener('focus', () => warm('covers'));

  document.addEventListener('infomancer:library-results-updated', () => {
    const view = currentView();
    setViewCookie(view);
    if (view === 'covers') {
      listSurface.dataset.librarySurfacePlaceholder = 'list';
    } else {
      coverSurface.dataset.librarySurfacePlaceholder = 'covers';
    }
  });

  let preferred = cookieView();
  if (!preferred) {
    try { preferred = validView(localStorage.getItem(STORAGE_KEY) || ''); } catch (_error) {}
  }
  applyView(preferred || currentView(), {persist: true, hydrate: true});
})();
