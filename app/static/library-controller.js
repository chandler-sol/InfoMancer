(() => {
  const input = document.getElementById('live-library-search');
  const table = document.querySelector('.library-table');
  const tableBody = table?.querySelector('tbody');
  const coverLibrary = document.getElementById('cover-library');
  if (!input || !table || !tableBody || !coverLibrary) return;

  const sourceFilter = document.getElementById('source-filter');
  const matchFilter = document.getElementById('match-filter');
  const gapFilter = document.getElementById('gap-filter');
  const genreFilter = document.getElementById('genre-filter');
  const titleTypeFilter = document.getElementById('title-type-filter');
  const favoriteFilter = document.getElementById('favorite-filter');
  const tagFilter = document.getElementById('tag-filter');
  const sortFilter = document.getElementById('sort-filter');
  const librarySuggestions = document.getElementById('library-search-suggestions');
  const filterSearch = document.getElementById('library-filter-search');
  const filterSearchToggle = document.getElementById('library-filter-search-toggle');
  const state = document.getElementById('search-state');
  const deselectLibraryTitles = document.getElementById('deselect-library-titles');
  const appendSortTitles = document.getElementById('append-sort-titles');
  const isLibrarian = document.body.classList.contains('role-librarian');
  const kind = table.dataset.libraryKind || 'all';
  const FILTER_KEYS = [
    'q', 'genre', 'title_type', 'root', 'match', 'gaps', 'person', 'person_name',
    'credit_role', 'favorite', 'tag', 'sort',
  ];

  let searchTimer = 0;
  let suggestionTimer = 0;
  let focusTimer = 0;
  let controller = null;
  let suggestionController = null;
  let requestNumber = 0;
  const selectionStorageKey = `infomancer-library-selection:${window.location.pathname}`;
  let selectionOrder = [];

  try {
    const stored = JSON.parse(sessionStorage.getItem(selectionStorageKey) || '[]');
    if (Array.isArray(stored)) {
      selectionOrder = stored
        .filter((value) => /^\d+$/.test(String(value)))
        .map(String)
        .slice(0, 1000);
    }
  } catch (_error) {}

  const uniqueChoices = () => {
    const choices = new Map();
    document.querySelectorAll('.library-title-choice').forEach((choice) => {
      if (!choices.has(String(choice.value))) choices.set(String(choice.value), choice);
    });
    return [...choices.values()];
  };

  const persistSelection = () => {
    try { sessionStorage.setItem(selectionStorageKey, JSON.stringify(selectionOrder)); } catch (_error) {}
  };

  const restoreStoredSelection = () => {
    const selected = new Set(selectionOrder);
    document.querySelectorAll('.library-title-choice').forEach((choice) => {
      choice.checked = selected.has(String(choice.value));
    });
  };

  const syncSelectionIndicators = () => {
    const choices = uniqueChoices();
    const visibleIds = new Set(choices.map((choice) => String(choice.value)));
    selectionOrder = selectionOrder.filter((titleId) => visibleIds.has(titleId));
    const selectedSet = new Set(selectionOrder);
    choices.forEach((choice) => {
      const id = String(choice.value);
      if (choice.checked && !selectedSet.has(id)) {
        selectedSet.add(id);
        selectionOrder.push(id);
      }
    });
    persistSelection();

    document.querySelectorAll('.library-title-choice').forEach((choice) => {
      choice.checked = selectedSet.has(String(choice.value));
    });

    const selectAll = document.getElementById('select-all-titles');
    if (selectAll) {
      const selectedCount = choices.reduce((count, choice) => count + Number(choice.checked), 0);
      selectAll.checked = choices.length > 0 && selectedCount === choices.length;
      selectAll.indeterminate = selectedCount > 0 && selectedCount < choices.length;
    }

    document.querySelectorAll('.letter-title-choice').forEach((letterChoice) => {
      const group = choices.filter((choice) => choice.dataset.initial === letterChoice.dataset.letter);
      const selectedCount = group.reduce((count, choice) => count + Number(choice.checked), 0);
      letterChoice.checked = group.length > 0 && selectedCount === group.length;
      letterChoice.indeterminate = selectedCount > 0 && selectedCount < group.length;
    });

    document.dispatchEvent(new CustomEvent('infomancer:library-selection-updated', {
      detail: {selectedIds: selectionOrder.slice()},
    }));
  };

  const setManySelected = (ids, checked) => {
    const targets = new Set(ids.map(String).filter(Boolean));
    if (!targets.size) return;
    document.querySelectorAll('.library-title-choice').forEach((choice) => {
      if (targets.has(String(choice.value))) choice.checked = checked;
    });
    selectionOrder = selectionOrder.filter((id) => !targets.has(id));
    if (checked) targets.forEach((id) => selectionOrder.push(id));
    syncSelectionIndicators();
  };

  const setTitleSelected = (titleId, checked) => setManySelected([titleId], checked);

  const setFilterSearchOpen = (open) => {
    if (!filterSearch || !filterSearchToggle) return;
    window.clearTimeout(focusTimer);
    focusTimer = 0;
    filterSearch.classList.toggle('open', open);
    filterSearchToggle.setAttribute('aria-expanded', String(open));
    filterSearchToggle.setAttribute('aria-label', open ? 'Search this library view' : 'Open library search');
    if (!open) {
      if (librarySuggestions) librarySuggestions.hidden = true;
      suggestionController?.abort();
      suggestionController = null;
      if (document.activeElement === input) input.blur();
      return;
    }
    focusTimer = window.setTimeout(() => {
      focusTimer = 0;
      if (filterSearch.classList.contains('open')) input.focus();
    }, 180);
  };

  const setParam = (url, key, value, defaultValue = '') => {
    if (value && value !== defaultValue) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  };

  const selectedText = (select) => (
    select?.value ? select.options[select.selectedIndex]?.text : ''
  );

  const currentRequestUrl = () => {
    const url = new URL(window.location.href);
    const query = input.value.trim();
    url.searchParams.delete('letter');
    setParam(url, 'q', query);
    setParam(url, 'genre', genreFilter?.value || '');
    setParam(url, 'title_type', titleTypeFilter?.value || '');
    setParam(url, 'favorite', favoriteFilter?.value || '');
    setParam(url, 'tag', tagFilter?.value || '');
    setParam(url, 'sort', sortFilter?.value || '', 'title');
    setParam(url, 'root', sourceFilter?.value || '');
    setParam(url, 'match', matchFilter?.value || '');
    setParam(url, 'gaps', gapFilter?.value || '');
    return url;
  };

  const updateFilterLinks = (url) => {
    document.querySelectorAll('.alphabet a, .catalog-tabs a').forEach((link) => {
      if (link.closest('.alphabet')) link.classList.remove('active');
      const linkUrl = new URL(link.href, window.location.origin);
      FILTER_KEYS.forEach((key) => {
        if (url.searchParams.has(key)) linkUrl.searchParams.set(key, url.searchParams.get(key));
        else linkUrl.searchParams.delete(key);
      });
      link.href = linkUrl.pathname + linkUrl.search;
    });
  };

  const updateSearchState = (query) => {
    if (!state) return;
    const filters = [
      query && `“${query}”`,
      selectedText(sourceFilter),
      selectedText(matchFilter),
      selectedText(gapFilter),
      selectedText(favoriteFilter),
      selectedText(tagFilter),
      genreFilter?.value || '',
      selectedText(titleTypeFilter),
    ].filter(Boolean);
    state.textContent = filters.length ? `Showing ${filters.join(' · ')}` : '';
  };

  const updateResults = async () => {
    controller?.abort();
    controller = new AbortController();
    const thisRequest = ++requestNumber;
    const url = currentRequestUrl();
    const query = input.value.trim();
    if (state) state.textContent = 'Searching…';
    tableBody.classList.add('updating');

    try {
      const response = await fetch(url.pathname + url.search, {
        credentials: 'same-origin',
        signal: controller.signal,
        headers: {'X-InfoMancer-Partial': 'library'},
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`Search returned ${response.status}`);
      const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
      const replacement = documentCopy.querySelector('.table-wrap tbody');
      const replacementCovers = documentCopy.getElementById('cover-library');
      if (!replacement || thisRequest !== requestNumber) return;

      tableBody.replaceChildren(...replacement.childNodes);
      if (replacementCovers) coverLibrary.replaceChildren(...replacementCovers.childNodes);
      restoreStoredSelection();
      syncSelectionIndicators();
      updateFilterLinks(url);
      url.searchParams.delete('inspect');
      history.replaceState({...history.state, workspaceInspectorTitleId: null}, '', url.pathname + url.search);
      document.dispatchEvent(new CustomEvent('infomancer:library-results-updated'));
      updateSearchState(query);
    } catch (error) {
      if (error.name !== 'AbortError' && thisRequest === requestNumber && state) {
        state.textContent = 'Search could not be updated. Press Search to retry.';
      }
    } finally {
      if (thisRequest === requestNumber) tableBody.classList.remove('updating');
    }
  };

  const updateLibrarySuggestions = async () => {
    if (!librarySuggestions || !filterSearch?.classList.contains('open')) return;
    const query = input.value.trim();
    if (query.length < 2) {
      librarySuggestions.hidden = true;
      return;
    }
    suggestionController?.abort();
    suggestionController = new AbortController();
    const url = new URL('/api/library-suggestions', window.location.origin);
    url.searchParams.set('q', query);
    url.searchParams.set('kind', kind);
    try {
      const response = await fetch(url, {
        credentials: 'same-origin',
        signal: suggestionController.signal,
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!filterSearch.classList.contains('open')) return;
      const options = (data.suggestions || []).map((suggestion) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.setAttribute('role', 'option');
        const strong = document.createElement('strong');
        const small = document.createElement('small');
        strong.textContent = suggestion.label;
        small.textContent = suggestion.detail
          ? `${suggestion.type} · ${suggestion.detail}`
          : suggestion.type;
        option.append(strong, small);
        option.addEventListener('click', () => {
          input.value = suggestion.value;
          librarySuggestions.hidden = true;
          updateResults();
        });
        return option;
      });
      librarySuggestions.replaceChildren(...options);
      librarySuggestions.hidden = options.length === 0;
    } catch (error) {
      if (error.name !== 'AbortError') librarySuggestions.hidden = true;
    }
  };

  input.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    window.clearTimeout(suggestionTimer);
    searchTimer = window.setTimeout(updateResults, 180);
    suggestionTimer = window.setTimeout(updateLibrarySuggestions, 45);
  });
  input.addEventListener('focus', () => {
    if (!filterSearch?.classList.contains('open')) {
      input.blur();
      return;
    }
    updateLibrarySuggestions();
  });
  input.addEventListener('keydown', async (event) => {
    if (event.key === 'ArrowDown' && librarySuggestions) {
      event.preventDefault();
      if (librarySuggestions.hidden) await updateLibrarySuggestions();
      librarySuggestions.querySelector('button')?.focus();
    } else if (event.key === 'Escape') {
      if (librarySuggestions) librarySuggestions.hidden = true;
      if (!input.value.trim()) setFilterSearchOpen(false);
    }
  });
  librarySuggestions?.addEventListener('keydown', (event) => {
    const options = [...librarySuggestions.querySelectorAll('button')];
    const index = options.indexOf(document.activeElement);
    if (event.key === 'ArrowDown' && options.length) {
      event.preventDefault();
      options[(index + 1) % options.length].focus();
    } else if (event.key === 'ArrowUp' && options.length) {
      event.preventDefault();
      if (index <= 0) input.focus();
      else options[index - 1].focus();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      librarySuggestions.hidden = true;
      input.focus();
    }
  });

  filterSearchToggle?.addEventListener('click', () => {
    if (!filterSearch?.classList.contains('open')) setFilterSearchOpen(true);
    else if (input.value.trim()) updateResults();
    else setFilterSearchOpen(false);
  });

  [genreFilter, titleTypeFilter, favoriteFilter, tagFilter, sortFilter, sourceFilter, matchFilter, gapFilter]
    .filter(Boolean)
    .forEach((filter) => filter.addEventListener('change', updateResults));

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('#select-all-titles')) {
      setManySelected(uniqueChoices().map((choice) => choice.value), target.checked);
      return;
    }
    if (target.matches('.letter-title-choice')) {
      const ids = uniqueChoices()
        .filter((choice) => choice.dataset.initial === target.dataset.letter)
        .map((choice) => choice.value);
      setManySelected(ids, target.checked);
      return;
    }
    if (target.matches('.library-title-choice')) setTitleSelected(target.value, target.checked);
  });

  deselectLibraryTitles?.addEventListener('click', () => {
    selectionOrder = [];
    document.querySelectorAll('.library-title-choice').forEach((choice) => { choice.checked = false; });
    syncSelectionIndicators();
  });

  appendSortTitles?.addEventListener('click', () => {
    if (selectionOrder.length < 2) return;
    const url = new URL('/titles/sort-titles', window.location.origin);
    selectionOrder.forEach((titleId) => url.searchParams.append('selected', titleId));
    url.searchParams.set('return_to', window.location.pathname + window.location.search);
    document.dispatchEvent(new CustomEvent('infomancer:open-dialog', {
      detail: {url: url.href, trigger: appendSortTitles},
    }));
  });

  document.addEventListener('click', (event) => {
    const titleLink = event.target.closest?.('a[href^="/titles/"]');
    if (titleLink && (titleLink.closest('#cover-library') || titleLink.closest('.library-table'))) {
      const row = titleLink.closest('[id^="title-"]');
      const anchor = row?.id ? `#${row.id}` : '';
      try {
        sessionStorage.setItem(
          'infomancerLibraryReturn',
          window.location.pathname + window.location.search + anchor,
        );
      } catch (_error) {}
    }

    if (isLibrarian && window.matchMedia('(hover: none), (pointer: coarse)').matches) {
      const coverLink = event.target.closest?.('.cover-card-link');
      if (coverLink) {
        const card = coverLink.closest('.cover-card');
        if (card && !card.classList.contains('actions-visible')) {
          event.preventDefault();
          document.querySelectorAll('.cover-card.actions-visible').forEach((other) => {
            if (other !== card) other.classList.remove('actions-visible');
          });
          card.classList.add('actions-visible');
          return;
        }
      } else if (!event.target.closest?.('.cover-card')) {
        document.querySelectorAll('.cover-card.actions-visible').forEach((card) => {
          card.classList.remove('actions-visible');
        });
      }
    }

    if (librarySuggestions && filterSearch && !filterSearch.contains(event.target)) {
      librarySuggestions.hidden = true;
    }
    if (filterSearch && !filterSearch.contains(event.target) && !input.value.trim()) {
      setFilterSearchOpen(false);
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !input.value.trim()) setFilterSearchOpen(false);
  });

  document.addEventListener('infomancer:library-results-updated', () => {
    restoreStoredSelection();
    syncSelectionIndicators();
  });

  restoreStoredSelection();
  syncSelectionIndicators();
})();
