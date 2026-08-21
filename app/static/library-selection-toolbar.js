(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const actions = document.getElementById('library-selection-actions');
  if (!toolbar || !actions) return;

  const viewToolbar = toolbar.querySelector('.library-view-toolbar');
  if (viewToolbar) toolbar.insertBefore(actions, viewToolbar);
  else toolbar.append(actions);
  toolbar.classList.add('library-selection-toolbar-ready');

  const selectedChoices = () => {
    const unique = new Map();
    document.querySelectorAll('.library-title-choice:checked').forEach((choice) => {
      if (!unique.has(choice.value)) unique.set(choice.value, choice);
    });
    return [...unique.values()];
  };
  const selectedIds = () => selectedChoices().map(choice => String(choice.value || '')).filter(Boolean);
  const rememberBulkMatchReturn = (kind) => {
    if (!['movie', 'tv'].includes(kind)) return;
    const form = document.getElementById('library-bulk-form');
    const returnTo = form?.querySelector('input[name="return_to"]')?.value
      || `${window.location.pathname}${window.location.search}`;
    try {
      window.sessionStorage.setItem(`infomancer:bulk-match-return:${kind}`, JSON.stringify({
        url: returnTo,
        at: Date.now(),
      }));
      window.sessionStorage.removeItem(`infomancer:bulk-match-return-pending:${kind}`);
    } catch (_) {}
  };

  const status = document.getElementById('search-state');
  const selectionCountLabel = document.getElementById('library-selection-count');
  const deselectButton = document.getElementById('deselect-library-titles');
  const sortButton = document.getElementById('append-sort-titles');
  const organizeButton = actions.querySelector('button[formaction="/titles/organize-bulk"]');
  const refreshButton = actions.querySelector('button[formaction="/metadata/queue"]');
  const analyzeLibraryMovies = document.getElementById('analyze-library-movies');
  const analyzeLibraryShows = document.getElementById('analyze-library-shows');

  if (deselectButton) {
    deselectButton.textContent = 'Clear';
    deselectButton.classList.add('library-selection-clear');
  }
  if (sortButton) {
    sortButton.textContent = 'Sort Titles';
    sortButton.classList.add('library-selection-secondary-command');
  }
  if (organizeButton) {
    organizeButton.textContent = 'Organize';
    organizeButton.classList.add('library-selection-command');
    organizeButton.title = 'Organize and tag selected titles';
    organizeButton.setAttribute('aria-label', 'Organize and tag selected titles');
  }
  if (refreshButton) {
    refreshButton.textContent = 'Refresh Metadata';
    refreshButton.classList.add('library-selection-secondary-command');
  }

  const favoriteButton = document.createElement('button');
  favoriteButton.type = 'button';
  favoriteButton.className = 'workspace-inspector-favorite library-bulk-favorite library-selection-command';
  favoriteButton.setAttribute('aria-pressed', 'false');
  favoriteButton.title = 'Add selected titles to Favorites';
  favoriteButton.innerHTML = '<span aria-hidden="true">★</span><small>Favorite</small>';

  const compareButton = document.createElement('button');
  compareButton.type = 'button';
  compareButton.className = 'button library-bulk-compare library-selection-command';
  compareButton.innerHTML = '<span class="library-selection-command-icon" aria-hidden="true">⇄</span><span>Compare</span>';
  compareButton.addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('infomancer:library-compare-selected'));
  });

  const matchMenu = document.createElement('details');
  matchMenu.className = 'library-bulk-match-menu';
  const matchSummary = document.createElement('summary');
  matchSummary.className = 'button library-bulk-match-summary';
  matchSummary.textContent = 'Match';
  const matchOptions = document.createElement('div');
  matchOptions.className = 'library-bulk-match-options';
  matchMenu.append(matchSummary, matchOptions);
  [analyzeLibraryMovies, analyzeLibraryShows].forEach((button) => {
    if (!button) return;
    button.classList.remove('primary');
    button.classList.add('library-selection-secondary-command');
    matchOptions.append(button);
    button.addEventListener('click', () => {
      rememberBulkMatchReturn(button.dataset.matchKind || '');
      matchMenu.removeAttribute('open');
    });
  });

  const selectionSummary = document.createElement('div');
  selectionSummary.className = 'library-selection-summary';
  if (selectionCountLabel) selectionSummary.append(selectionCountLabel);
  if (deselectButton) selectionSummary.append(deselectButton);

  const primaryCommands = document.createElement('div');
  primaryCommands.className = 'library-selection-primary';
  primaryCommands.append(favoriteButton);
  if (organizeButton) primaryCommands.append(organizeButton);
  primaryCommands.append(compareButton);

  const moreMenu = document.createElement('details');
  moreMenu.className = 'library-bulk-more-menu';
  const moreSummary = document.createElement('summary');
  moreSummary.className = 'button library-selection-command library-bulk-more-summary';
  moreSummary.innerHTML = '<span class="library-selection-command-icon" aria-hidden="true">•••</span><span>More</span>';
  const moreOptions = document.createElement('div');
  moreOptions.className = 'library-bulk-more-options';
  if (sortButton) moreOptions.append(sortButton);
  if (refreshButton) moreOptions.append(refreshButton);
  moreOptions.append(matchMenu);
  moreMenu.append(moreSummary, moreOptions);
  primaryCommands.append(moreMenu);

  /* Multi-selection is a command bar, not a second form. Keep the selection state
     and the four most useful entry points visible; lower-frequency operations live
     under More so phone layouts stay roughly two compact rows high. */
  actions.replaceChildren(selectionSummary, primaryCommands);

  const markFavoriteInPlace = (titleId) => {
    document.querySelectorAll(`[data-workspace-title-id="${CSS.escape(String(titleId))}"]`).forEach((item) => {
      item.querySelectorAll('.cover-favorite-button').forEach((button) => {
        button.classList.add('active');
        button.title = 'Remove from favorites';
        const title = item.querySelector('.cover-card-link > strong, .title-link')?.textContent?.trim() || 'title';
        button.setAttribute('aria-label', `Remove ${title} from favorites`);
      });
      item.querySelectorAll('.favorite-action').forEach((button) => {
        button.classList.add('active');
        const star = button.querySelector('span');
        button.replaceChildren();
        if (star) button.append(star);
        button.append(document.createTextNode('Remove favorite'));
      });
    });
  };

  favoriteButton.addEventListener('click', async () => {
    const ids = selectedIds();
    if (ids.length < 2 || favoriteButton.disabled) return;
    favoriteButton.disabled = true;
    favoriteButton.title = 'Adding selected titles to Favorites…';
    const body = new FormData();
    ids.forEach(id => body.append('selected', id));
    const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
    try {
      const response = await fetch('/titles/favorite-bulk', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        body,
        headers: {
          'Accept': 'application/json',
          'X-InfoMancer-Async': '1',
          ...(csrf ? {'X-CSRF-Token': csrf} : {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      (data.title_ids || ids).forEach(markFavoriteInPlace);
      favoriteButton.classList.add('active');
      favoriteButton.setAttribute('aria-pressed', 'true');
      favoriteButton.title = 'Selected titles added to Favorites';
      if (status) status.textContent = data.detail || `Added ${ids.length} selected titles to Favorites.`;
    } catch (error) {
      favoriteButton.title = 'Add selected titles to Favorites';
      if (status) status.textContent = error.message || 'Selected titles could not be added to Favorites.';
    } finally {
      favoriteButton.disabled = false;
    }
  });

  const sync = () => {
    const choices = selectedChoices();
    const count = choices.length;
    const shouldHide = count < 2;
    if (actions.hidden !== shouldHide) actions.hidden = shouldHide;
    toolbar.classList.toggle('has-selection-actions', !shouldHide);
    if (selectionCountLabel) selectionCountLabel.textContent = `${count} selected`;
    if (!shouldHide) {
      favoriteButton.classList.remove('active');
      favoriteButton.setAttribute('aria-pressed', 'false');
      favoriteButton.title = 'Add selected titles to Favorites';
    } else {
      moreMenu.removeAttribute('open');
      matchMenu.removeAttribute('open');
    }

    const unmatched = choices.filter(choice => choice.dataset.matched !== 'true');
    const movies = unmatched.filter(choice => choice.dataset.kind === 'movie');
    const shows = unmatched.filter(choice => choice.dataset.kind === 'tv');

    if (analyzeLibraryMovies) {
      analyzeLibraryMovies.hidden = movies.length === 0;
      analyzeLibraryMovies.textContent = `Movies (${movies.length})`;
    }
    if (analyzeLibraryShows) {
      analyzeLibraryShows.hidden = shows.length === 0;
      analyzeLibraryShows.textContent = `TV Shows (${shows.length})`;
    }
    matchMenu.hidden = unmatched.length === 0;
    if (matchMenu.hidden) matchMenu.removeAttribute('open');
  };

  organizeButton?.addEventListener('click', (event) => {
    const ids = selectedIds();
    if (ids.length < 2) return;
    event.preventDefault();
    const body = new FormData();
    ids.forEach(id => body.append('selected', id));
    document.dispatchEvent(new CustomEvent('infomancer:open-dialog', {
      detail: {
        url: '/titles/organize-bulk',
        trigger: organizeButton,
        method: 'POST',
        body,
      },
    }));
  });

  document.addEventListener('infomancer:library-bulk-organized', (event) => {
    if (status) status.textContent = event.detail?.message || 'Organization saved for selected titles.';
  });

  document.addEventListener('change', (event) => {
    if (event.target.matches('.library-title-choice, .letter-title-choice, #select-all-titles')) {
      queueMicrotask(sync);
    }
  });
  document.addEventListener('infomancer:library-results-updated', () => queueMicrotask(sync));
  document.addEventListener('infomancer:library-selection-updated', () => queueMicrotask(sync));

  document.addEventListener('pointerdown', (event) => {
    if (moreMenu.open && !moreMenu.contains(event.target)) moreMenu.removeAttribute('open');
    if (matchMenu.open && !matchMenu.contains(event.target)) matchMenu.removeAttribute('open');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (matchMenu.open) matchMenu.removeAttribute('open');
    if (moreMenu.open) {
      moreMenu.removeAttribute('open');
      moreSummary.focus();
    }
  });

  sync();
})();
