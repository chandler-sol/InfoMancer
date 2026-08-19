(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const actions = document.getElementById('library-selection-actions');
  if (!toolbar || !actions) return;

  const viewToolbar = toolbar.querySelector('.library-view-toolbar');
  if (viewToolbar) toolbar.insertBefore(actions, viewToolbar);
  else toolbar.append(actions);
  toolbar.classList.add('library-selection-toolbar-ready');

  const selectedIds = () => [
    ...new Set(
      [...document.querySelectorAll('.library-title-choice:checked')]
        .map(choice => String(choice.value || ''))
        .filter(Boolean),
    ),
  ];

  const selectionCount = () => selectedIds().length;
  const status = document.getElementById('search-state');
  const organizeButton = actions.querySelector('button[formaction="/titles/organize-bulk"]');
  const deselectButton = document.getElementById('deselect-library-titles');

  const favoriteButton = document.createElement('button');
  favoriteButton.type = 'button';
  favoriteButton.className = 'button library-bulk-favorite';
  favoriteButton.textContent = 'Add to Favorites';
  if (deselectButton?.nextSibling) deselectButton.after(favoriteButton);
  else actions.append(favoriteButton);

  const sync = () => {
    const count = selectionCount();
    /* A single selection already has the Inspector and normal title actions. The
       command bar is specifically the bulk-action surface, so it starts at two. */
    actions.hidden = count < 2;
    toolbar.classList.toggle('has-selection-actions', count >= 2);
  };

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

      const titleLink = item.querySelector('.title-link');
      if (titleLink) {
        const copy = titleLink.parentElement;
        let organization = copy?.querySelector(':scope > .title-organization');
        if (!organization && copy) {
          organization = document.createElement('div');
          organization.className = 'title-organization';
          titleLink.insertAdjacentElement('afterend', organization);
        }
        if (organization && !organization.querySelector('.favorite-star')) {
          const star = document.createElement('span');
          star.className = 'favorite-star active';
          star.title = 'Favorite';
          star.textContent = '★';
          organization.prepend(star);
        }
      }
    });
  };

  favoriteButton.addEventListener('click', async () => {
    const ids = selectedIds();
    if (ids.length < 2) return;
    const original = favoriteButton.textContent;
    favoriteButton.disabled = true;
    favoriteButton.textContent = 'Adding…';
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
      favoriteButton.textContent = 'Added to Favorites ✓';
      if (status) status.textContent = data.detail || `Added ${ids.length} selected titles to Favorites.`;
      window.setTimeout(() => {
        if (!favoriteButton.isConnected) return;
        favoriteButton.disabled = false;
        favoriteButton.textContent = original;
      }, 1600);
    } catch (error) {
      favoriteButton.disabled = false;
      favoriteButton.textContent = original;
      if (status) status.textContent = error.message || 'Selected titles could not be added to Favorites.';
    }
  });

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

  /* The legacy Library controller owns the selection state and still toggles this
     element for one title. Observe that result and immediately enforce the newer
     bulk-only visibility rule without duplicating the selection model. */
  new MutationObserver(sync).observe(actions, {
    attributes: true,
    attributeFilter: ['hidden'],
  });
  document.addEventListener('change', (event) => {
    if (event.target.matches('.library-title-choice, .letter-title-choice, #select-all-titles')) {
      queueMicrotask(sync);
    }
  });
  document.addEventListener('infomancer:library-results-updated', () => queueMicrotask(sync));
  sync();
})();
