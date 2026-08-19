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

  const status = document.getElementById('search-state');
  const selectionCountLabel = document.getElementById('library-selection-count');
  const deselectButton = document.getElementById('deselect-library-titles');
  const sortButton = document.getElementById('append-sort-titles');
  const organizeButton = actions.querySelector('button[formaction="/titles/organize-bulk"]');
  const refreshButton = actions.querySelector('button[formaction="/metadata/queue"]');
  const analyzeLibraryMovies = document.getElementById('analyze-library-movies');
  const analyzeLibraryShows = document.getElementById('analyze-library-shows');

  if (deselectButton) deselectButton.textContent = 'Deselect';
  if (sortButton) sortButton.textContent = 'Sort Titles';
  if (refreshButton) refreshButton.textContent = 'Refresh Metadata';

  const compareButton = document.createElement('button');
  compareButton.type = 'button';
  compareButton.className = 'button library-bulk-compare';
  compareButton.textContent = 'Compare';
  compareButton.addEventListener('click', () => {
    document.dispatchEvent(new CustomEvent('infomancer:library-compare-selected'));
  });

  const separator = () => {
    const node = document.createElement('span');
    node.className = 'library-bulk-separator';
    node.setAttribute('aria-hidden', 'true');
    return node;
  };

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
    matchOptions.append(button);
    button.addEventListener('click', () => matchMenu.removeAttribute('open'));
  });

  /* Rebuild the row deliberately so related actions read as groups instead of a
     growing collection of unrelated buttons. The legacy controller still owns the
     actual form submissions; this layer only controls presentation and modal entry. */
  actions.replaceChildren();
  if (selectionCountLabel) actions.append(selectionCountLabel);
  if (deselectButton) actions.append(deselectButton);
  actions.append(separator());
  if (sortButton) actions.append(sortButton);
  if (organizeButton) actions.append(organizeButton);
  actions.append(separator(), compareButton);
  if (refreshButton) actions.append(refreshButton);
  actions.append(matchMenu);

  const sync = () => {
    const choices = selectedChoices();
    const count = choices.length;
    const shouldHide = count < 2;
    if (actions.hidden !== shouldHide) actions.hidden = shouldHide;
    toolbar.classList.toggle('has-selection-actions', !shouldHide);
    if (selectionCountLabel) selectionCountLabel.textContent = `${count} selected`;

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
    if (matchMenu.open && !matchMenu.contains(event.target)) matchMenu.removeAttribute('open');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && matchMenu.open) {
      matchMenu.removeAttribute('open');
      matchSummary.focus();
    }
  });

  sync();
})();
