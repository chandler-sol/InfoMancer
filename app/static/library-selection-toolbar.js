(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const actions = document.getElementById('library-selection-actions');
  if (!toolbar || !actions) return;

  const coverLibrary = document.getElementById('cover-library');
  const libraryTable = document.querySelector('.library-table');
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
  const titleIdFor = (item) => String(item?.dataset?.workspaceTitleId || '');
  const visibleItems = () => {
    const selector = coverLibrary && !coverLibrary.hidden ? '.cover-card' : '.library-title-row';
    return [...document.querySelectorAll(selector)].filter(item => titleIdFor(item));
  };
  const rangeIds = (fromId, toId) => {
    const items = visibleItems();
    const start = items.findIndex(item => titleIdFor(item) === String(fromId));
    const finish = items.findIndex(item => titleIdFor(item) === String(toId));
    if (start < 0 || finish < 0) return [String(toId)];
    const [low, high] = start < finish ? [start, finish] : [finish, start];
    return items.slice(low, high + 1).map(titleIdFor).filter(Boolean);
  };
  const setTitleChecked = (titleId, checked) => {
    const choices = [...document.querySelectorAll(`.library-title-choice[value="${CSS.escape(String(titleId))}"]`)];
    if (!choices.length) return;
    const changed = choices.some(choice => choice.checked !== checked);
    choices.forEach(choice => { choice.checked = checked; });
    if (changed) choices[0].dispatchEvent(new Event('change', {bubbles: true}));
  };
  const choiceIsChecked = (titleId) => document.querySelector(
    `.library-title-choice[value="${CSS.escape(String(titleId))}"]:checked`,
  ) !== null;
  const csrfToken = () => document.body.dataset.csrfToken
    || document.querySelector('input[name="csrf_token"]')?.value
    || '';

  const rememberBulkMatchReturn = (kind) => {
    if (!['movie', 'tv'].includes(kind)) return;
    // Live Library filtering updates the browser URL without rerendering the form,
    // so the address bar is the authoritative return destination here.
    const returnTo = `${window.location.pathname}${window.location.search}`;
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

  const titleIsFavorite = (titleId) => {
    const id = String(titleId);
    const choices = [...document.querySelectorAll(`.library-title-choice[value="${CSS.escape(id)}"]`)];
    const known = choices.find(choice => choice.dataset.favorite === 'true' || choice.dataset.favorite === 'false');
    if (known) return known.dataset.favorite === 'true';
    const selector = `[data-workspace-title-id="${CSS.escape(id)}"]`;
    return [...document.querySelectorAll(selector)].some((item) => Boolean(
      item.querySelector('.cover-favorite-button.active, .favorite-action.active, .favorite-star.active')
    ));
  };

  const setFavoriteInPlace = (titleId, favorite) => {
    const id = String(titleId);
    document.querySelectorAll(`.library-title-choice[value="${CSS.escape(id)}"]`).forEach((choice) => {
      choice.dataset.favorite = String(Boolean(favorite));
    });
    const selector = `[data-workspace-title-id="${CSS.escape(id)}"]`;
    document.querySelectorAll(selector).forEach((item) => {
      item.dataset.favorite = String(Boolean(favorite));
      const title = item.querySelector('.cover-card-link > strong, .title-link')?.textContent?.trim() || 'title';
      item.querySelectorAll('.cover-favorite-button').forEach((button) => {
        button.classList.toggle('active', favorite);
        button.title = favorite ? 'Remove from favorites' : 'Add to favorites';
        button.setAttribute(
          'aria-label',
          `${favorite ? 'Remove' : 'Add'} ${title} ${favorite ? 'from' : 'to'} favorites`,
        );
      });
      item.querySelectorAll('.favorite-action').forEach((button) => {
        button.classList.toggle('active', favorite);
        const star = button.querySelector('span');
        button.replaceChildren();
        if (star) button.append(star);
        button.append(document.createTextNode(favorite ? 'Remove favorite' : 'Add favorite'));
      });

      if (item.matches('.library-title-row')) {
        const titleCopy = item.querySelector('.library-title-cell .title-cell > div');
        let organization = titleCopy?.querySelector('.title-organization');
        let star = organization?.querySelector('.favorite-star');
        if (favorite && titleCopy && !star) {
          if (!organization) {
            organization = document.createElement('div');
            organization.className = 'title-organization';
            const path = titleCopy.querySelector('.library-file-path');
            if (path) titleCopy.insertBefore(organization, path);
            else titleCopy.append(organization);
          }
          star = document.createElement('span');
          star.className = 'favorite-star active';
          star.title = 'Favorite';
          star.textContent = '★';
          organization.prepend(star);
        } else if (!favorite && star) {
          star.remove();
          if (organization && !organization.children.length && !organization.textContent.trim()) {
            organization.remove();
          }
        }
      }
    });
  };

  const renderFavoriteButton = (allFavorite) => {
    favoriteButton.classList.toggle('active', allFavorite);
    favoriteButton.setAttribute('aria-pressed', String(allFavorite));
    favoriteButton.title = allFavorite
      ? 'Remove selected titles from Favorites'
      : 'Add selected titles to Favorites';
    const label = favoriteButton.querySelector('small');
    if (label) label.textContent = allFavorite ? 'Unfavorite' : 'Favorite';
  };

  const syncFavoriteButton = (choices = selectedChoices()) => {
    const allFavorite = choices.length >= 2 && choices.every(choice => titleIsFavorite(choice.value));
    renderFavoriteButton(allFavorite);
    return allFavorite;
  };

  favoriteButton.addEventListener('click', async () => {
    const choices = selectedChoices();
    const ids = choices.map(choice => String(choice.value || '')).filter(Boolean);
    if (ids.length < 2 || favoriteButton.disabled) return;
    favoriteButton.disabled = true;
    favoriteButton.title = 'Updating selected Favorites…';
    const body = new FormData();
    ids.forEach(id => body.append('selected', id));
    // Let the server derive the next state from the persisted favorites. This keeps
    // the command reversible even if a stale DOM or a live filter briefly disagrees.
    body.append('favorite', 'toggle');
    const csrf = csrfToken();
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
      const favorite = Boolean(data.favorite);
      (data.title_ids || ids).forEach(id => setFavoriteInPlace(id, favorite));
      // The endpoint applies one state to the entire selected set, so render that
      // returned state directly instead of re-inferring it from transient markup.
      renderFavoriteButton(favorite);
      if (status) status.textContent = data.detail || (
        favorite
          ? `Added ${ids.length} selected titles to Favorites.`
          : `Removed ${ids.length} selected titles from Favorites.`
      );
    } catch (error) {
      syncFavoriteButton(choices);
      if (status) status.textContent = error.message || 'Selected titles could not be updated in Favorites.';
    } finally {
      favoriteButton.disabled = false;
    }
  });

  const toggleSingleFavorite = async (button, item) => {
    const titleId = titleIdFor(item);
    if (!titleId || button.disabled) return;
    button.disabled = true;
    const csrf = csrfToken();
    try {
      const response = await fetch(`/api/titles/${encodeURIComponent(titleId)}/favorite`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          'Accept': 'application/json',
          'X-InfoMancer-Async': '1',
          ...(csrf ? {'X-CSRF-Token': csrf} : {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      setFavoriteInPlace(titleId, Boolean(data.favorite));
      syncFavoriteButton();
      if (status) status.textContent = data.detail || 'Favorite updated.';
    } catch (error) {
      if (status) status.textContent = error.message || 'Favorite could not be updated.';
    } finally {
      button.disabled = false;
    }
  };

  const sync = () => {
    const choices = selectedChoices();
    const count = choices.length;
    const shouldHide = count < 2;
    if (actions.hidden !== shouldHide) actions.hidden = shouldHide;
    toolbar.classList.toggle('has-selection-actions', !shouldHide);
    if (selectionCountLabel) selectionCountLabel.textContent = `${count} selected`;
    if (!shouldHide) {
      syncFavoriteButton(choices);
    } else {
      renderFavoriteButton(false);
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

  /* Selection gestures are owned at the window capture layer so the older Library
     click/drag handlers never get a chance to reinterpret a range-removal gesture.
     Dragging from an unselected cover selects; dragging from a selected cover
     deselects. Shift-click follows the same rule at the target end of the range. */
  let gestureAnchorId = '';
  let pointerGesture = null;
  let shiftPointer = null;
  let applyingGesture = false;
  let suppressGestureClick = false;
  const dragThreshold = 7;
  const gestureInteractive = (target) => target?.closest?.(
    'input, button, summary, details, form, select, textarea, .item-action-menu, .cover-select-control',
  );

  const applyRangeState = (fromId, toId, checked) => {
    applyingGesture = true;
    rangeIds(fromId, toId).forEach(id => setTitleChecked(id, checked));
    applyingGesture = false;
    gestureAnchorId = String(toId);
    sync();
  };

  window.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;

    const favoriteControl = event.target.closest?.('.cover-favorite-button, .favorite-action');
    if (favoriteControl) return;

    const checkbox = event.target.matches?.('.library-title-choice')
      ? event.target
      : event.target.closest?.('.cover-select-control')?.querySelector('.library-title-choice');
    if (checkbox && event.shiftKey) {
      shiftPointer = {
        id: String(checkbox.value || ''),
        wasChecked: checkbox.checked,
      };
      return;
    }

    const card = event.target.closest?.('.cover-card');
    if (!card || gestureInteractive(event.target)) return;
    const titleId = titleIdFor(card);
    if (!titleId) return;

    // Prevent the previous cover drag controller from arming itself. A normal click
    // is still allowed through later if the pointer never crosses the drag threshold.
    event.stopPropagation();
    pointerGesture = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startId: titleId,
      lastId: titleId,
      startSelected: choiceIsChecked(titleId),
      targetChecked: !choiceIsChecked(titleId),
      additive: event.ctrlKey || event.metaKey,
      active: false,
    };
  }, true);

  window.addEventListener('dragstart', (event) => {
    if (pointerGesture && event.target.closest?.('.cover-card')) event.preventDefault();
  }, true);

  window.addEventListener('pointermove', (event) => {
    if (!pointerGesture || event.pointerId !== pointerGesture.pointerId) return;
    if (!pointerGesture.active) {
      const distance = Math.hypot(
        event.clientX - pointerGesture.startX,
        event.clientY - pointerGesture.startY,
      );
      if (distance < dragThreshold) return;
      pointerGesture.active = true;
      document.body.classList.add('library-drag-selecting');
      if (pointerGesture.targetChecked && !pointerGesture.additive && !pointerGesture.startSelected) {
        selectedIds().forEach(id => {
          if (id !== pointerGesture.startId) setTitleChecked(id, false);
        });
      }
      setTitleChecked(pointerGesture.startId, pointerGesture.targetChecked);
    }

    event.preventDefault();
    event.stopPropagation();
    const card = document.elementFromPoint(event.clientX, event.clientY)?.closest?.('.cover-card');
    const titleId = titleIdFor(card);
    if (!titleId || titleId === pointerGesture.lastId) return;
    rangeIds(pointerGesture.lastId, titleId).forEach(id => {
      setTitleChecked(id, pointerGesture.targetChecked);
    });
    pointerGesture.lastId = titleId;
  }, {capture: true, passive: false});

  const finishPointerGesture = (event) => {
    if (!pointerGesture || event.pointerId !== pointerGesture.pointerId) return;
    const finished = pointerGesture;
    pointerGesture = null;
    document.body.classList.remove('library-drag-selecting');
    if (!finished.active) return;
    event.preventDefault();
    event.stopPropagation();
    gestureAnchorId = finished.lastId;
    suppressGestureClick = true;
    sync();
    window.setTimeout(() => { suppressGestureClick = false; }, 0);
  };
  window.addEventListener('pointerup', finishPointerGesture, true);
  window.addEventListener('pointercancel', finishPointerGesture, true);

  window.addEventListener('click', (event) => {
    const favoriteControl = event.target.closest?.('.cover-favorite-button, .favorite-action');
    if (favoriteControl) {
      const item = favoriteControl.closest('[data-workspace-title-id]');
      if (!item) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      void toggleSingleFavorite(favoriteControl, item);
      return;
    }

    const item = event.target.closest?.('.cover-card, .library-title-row');
    if (!item) return;
    const titleId = titleIdFor(item);
    if (!titleId) return;

    if (suppressGestureClick && item.matches('.cover-card')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressGestureClick = false;
      return;
    }

    const checkbox = event.target.matches?.('.library-title-choice')
      ? event.target
      : event.target.closest?.('.cover-select-control')?.querySelector('.library-title-choice');
    if (event.shiftKey && checkbox) {
      const pending = shiftPointer?.id === String(checkbox.value || '') ? shiftPointer : null;
      const targetChecked = pending ? !pending.wasChecked : !checkbox.checked;
      const anchor = gestureAnchorId || selectedIds().find(id => id !== titleId) || titleId;
      shiftPointer = null;
      event.preventDefault();
      event.stopImmediatePropagation();
      applyRangeState(anchor, titleId, targetChecked);
      return;
    }

    if (event.shiftKey && !gestureInteractive(event.target)) {
      const targetChecked = !choiceIsChecked(titleId);
      const anchor = gestureAnchorId || selectedIds().find(id => id !== titleId) || titleId;
      event.preventDefault();
      event.stopImmediatePropagation();
      applyRangeState(anchor, titleId, targetChecked);
    }
  }, true);

  document.addEventListener('change', (event) => {
    if (event.target.matches('.library-title-choice, .letter-title-choice, #select-all-titles')) {
      if (!applyingGesture && event.target.matches('.library-title-choice')) {
        gestureAnchorId = String(event.target.value || '');
      }
      queueMicrotask(sync);
    }
  });
  document.addEventListener('infomancer:library-results-updated', () => {
    gestureAnchorId = '';
    pointerGesture = null;
    shiftPointer = null;
    document.body.classList.remove('library-drag-selecting');
    queueMicrotask(sync);
  });
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
