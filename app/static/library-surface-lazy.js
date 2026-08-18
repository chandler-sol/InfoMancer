(() => {
  const listSurface = document.querySelector('.library-table');
  const coverSurface = document.getElementById('cover-library');
  const listButton = document.getElementById('library-list-view');
  const coverButton = document.getElementById('library-cover-view');
  if (!listSurface || !coverSurface || !listButton || !coverButton) return;

  const cookieName = 'infomancer_library_view';
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

  const hydrateSurface = async (view) => {
    setViewCookie(view);
    const surface = view === 'covers' ? coverSurface : listSurface;
    if (surface.dataset.librarySurfacePlaceholder !== view) return;
    if (surface.dataset.librarySurfaceLoading === '1') return;

    surface.dataset.librarySurfaceLoading = '1';
    markLoading(view);
    try {
      const response = await fetch(window.location.pathname + window.location.search, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          'X-InfoMancer-Library-View': view,
          'X-InfoMancer-Partial': 'library-surface',
        },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const fresh = new DOMParser().parseFromString(await response.text(), 'text/html');

      if (view === 'covers') {
        const replacement = fresh.getElementById('cover-library');
        if (!replacement || replacement.dataset.librarySurfacePlaceholder === 'covers') {
          throw new Error('Cover surface was not returned');
        }
        coverSurface.replaceChildren(...replacement.childNodes);
        rememberTitleReturn(coverSurface);
      } else {
        const replacement = fresh.querySelector('.library-table');
        const replacementTable = replacement?.querySelector('table');
        const replacementHead = replacementTable?.querySelector('thead');
        const replacementBody = replacementTable?.querySelector('tbody');
        const currentTable = listSurface.querySelector('table');
        const currentBody = currentTable?.querySelector('tbody');
        if (!replacementBody || replacement?.dataset.librarySurfacePlaceholder === 'list' || !currentTable || !currentBody) {
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
      document.dispatchEvent(new CustomEvent('infomancer:library-results-updated'));
    } catch (_error) {
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
    } finally {
      delete surface.dataset.librarySurfaceLoading;
    }
  };

  listButton.addEventListener('click', () => hydrateSurface('list'));
  coverButton.addEventListener('click', () => hydrateSurface('covers'));

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
