(() => {
  const listSurface = document.querySelector('.library-table');
  const coverSurface = document.getElementById('cover-library');
  const listButton = document.getElementById('library-list-view');
  const coverButton = document.getElementById('library-cover-view');
  if (!listSurface || !coverSurface || !listButton || !coverButton) return;

  const cookieName = 'infomancer_library_view';
  const inflight = new Map();

  const setViewCookie = (view) => {
    if (!['list', 'covers'].includes(view)) return;
    document.cookie = `${cookieName}=${view}; Path=/; SameSite=Lax; Max-Age=31536000`;
  };

  const currentView = () => coverSurface.hidden ? 'list' : 'covers';

  const rememberTitleReturn = (surface) => {
    surface.querySelectorAll('a[href^="/titles/"]').forEach((link) => {
      if (link.dataset.libraryReturnReady === '1') return;
      link.dataset.libraryReturnReady = '1';
      link.addEventListener('click', () => {
        const row = link.closest("[id^='title-']");
        const anchor = row?.id ? `#${row.id}` : '';
        try {
          sessionStorage.setItem(
            'infomancerLibraryReturn',
            window.location.pathname + window.location.search + anchor,
          );
        } catch (_error) {}
      });
    });
  };

  const bindHydratedListControls = () => {
    const selectAll = listSurface.querySelector('#select-all-titles');
    if (!selectAll || selectAll.dataset.lazyBound === '1') return;
    selectAll.dataset.lazyBound = '1';
    selectAll.addEventListener('change', () => {
      const unique = new Map();
      listSurface.querySelectorAll('.library-title-choice').forEach((choice) => unique.set(choice.value, choice));
      unique.forEach((choice) => {
        if (choice.checked === selectAll.checked) return;
        choice.checked = selectAll.checked;
        choice.dispatchEvent(new Event('change', {bubbles: true}));
      });
    });
    listSurface.addEventListener('change', (event) => {
      if (!event.target.matches('.library-title-choice, .letter-title-choice')) return;
      queueMicrotask(() => {
        const choices = [...listSurface.querySelectorAll('.library-title-choice')];
        selectAll.checked = choices.length > 0 && choices.every((choice) => choice.checked);
        selectAll.indeterminate = choices.some((choice) => choice.checked) && !selectAll.checked;
      });
    });
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

  /* The server response is still a complete Library document for compatibility,
     but parsing the whole application shell just to hydrate one hidden surface is
     wasteful. Extract the requested section first, then parse only that fragment. */
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

  const hydrateSurface = async (view, {remember = true, announce = true, showLoading = true} = {}) => {
    if (remember) setViewCookie(view);
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
        rememberTitleReturn(coverSurface);
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
        if (replacement.dataset.libraryKind) listSurface.dataset.libraryKind = replacement.dataset.libraryKind;
        bindHydratedListControls();
        rememberTitleReturn(listSurface);
      }

      delete surface.dataset.librarySurfacePlaceholder;
      if (announce) document.dispatchEvent(new CustomEvent('infomancer:library-results-updated'));
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
        if (body) body.innerHTML = '<tr><td colspan="7" class="empty">List view could not be loaded. Try again.</td></tr>';
      }
      return false;
    } finally {
      delete surface.dataset.librarySurfaceLoading;
    }
  };

  listButton.addEventListener('click', () => hydrateSurface('list'));
  coverButton.addEventListener('click', () => hydrateSurface('covers'));

  /* Start hydrating the opposite surface only after the user shows intent by
     hovering or keyboard-focusing its toggle. The preference is not changed and
     the hidden surface stays hidden, so an eventual click can switch immediately. */
  const warm = (view) => {
    const surface = view === 'covers' ? coverSurface : listSurface;
    if (surface.dataset.librarySurfacePlaceholder !== view) return;
    hydrateSurface(view, {remember: false, announce: false, showLoading: false});
  };
  listButton.addEventListener('pointerenter', () => warm('list'), {passive: true});
  coverButton.addEventListener('pointerenter', () => warm('covers'), {passive: true});
  listButton.addEventListener('focus', () => warm('list'));
  coverButton.addEventListener('focus', () => warm('covers'));

  /* The built-in live filter updates both surface child lists from one response.
     The server intentionally leaves the inactive surface empty, so mark that side
     as lazy again after every filter/search refresh. */
  document.addEventListener('infomancer:library-results-updated', () => {
    const view = currentView();
    setViewCookie(view);
    if (view === 'covers') {
      listSurface.dataset.librarySurfacePlaceholder = 'list';
    } else {
      coverSurface.dataset.librarySurfacePlaceholder = 'covers';
    }
  });

  /* If the saved browser preference changed before this response arrived, the
     server can legitimately send the opposite surface. Hydrate the visible choice
     immediately rather than leaving a lightweight placeholder on screen. */
  hydrateSurface(currentView());
})();
