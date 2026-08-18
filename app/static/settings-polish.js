(() => {
  const nav = document.querySelector('.settings-section-nav');
  if (!nav) return;

  document.body.classList.add('settings-workspace-polished');
  const active = nav.querySelector('a.active');
  const path = active ? new URL(active.href, window.location.href).pathname : window.location.pathname;
  const section = path === '/sources'
    ? 'sources'
    : path.endsWith('/scheduled-tasks')
      ? 'scheduled-tasks'
      : path.endsWith('/recovery')
        ? 'recovery'
        : path.endsWith('/metadata')
          ? 'metadata'
          : path.endsWith('/external-search')
            ? 'external-search'
            : path.endsWith('/system')
              ? 'system'
              : 'general';
  document.body.classList.add(`settings-section-${section}`);

  const balanceGeneral = () => {
    const form = document.querySelector('form.settings-page-grid[action="/settings/general"]');
    if (!form) return;
    const cards = [...form.querySelectorAll(':scope > .settings-card:not(.full-width)')];
    if (cards.length < 2) return;

    const [regionalCard, libraryCard] = cards;
    regionalCard.classList.add('settings-general-card');
    libraryCard.classList.add('settings-general-card');

    const regionalHead = regionalCard.querySelector(':scope > div');
    const libraryHead = libraryCard.querySelector(':scope > div');
    if (regionalHead) {
      const eyebrow = regionalHead.querySelector('.eyebrow');
      const title = regionalHead.querySelector('h2');
      const copy = regionalHead.querySelector('.muted');
      if (eyebrow) eyebrow.textContent = 'REGION & INTERFACE';
      if (title) title.textContent = 'Regional & display';
      if (copy) copy.textContent = 'Choose how InfoMancer presents time and which Library layout a new browser sees first.';
    }
    if (libraryHead) {
      const eyebrow = libraryHead.querySelector('.eyebrow');
      const title = libraryHead.querySelector('h2');
      const copy = libraryHead.querySelector('.muted');
      if (eyebrow) eyebrow.textContent = 'LIBRARY BROWSING';
      if (title) title.textContent = 'Browsing defaults';
      if (copy) copy.textContent = 'Set the visual density and starting behavior used when browsing your catalog.';
    }

    const defaultView = libraryCard.querySelector(':scope > label select[name="default_library_view"]')?.closest('label');
    if (defaultView) regionalCard.append(defaultView);

    for (const card of [regionalCard, libraryCard]) {
      const directLabels = [...card.children].filter((child) => child.tagName === 'LABEL');
      if (!directLabels.length) continue;
      const stack = document.createElement('div');
      stack.className = 'settings-field-stack';
      directLabels[0].before(stack);
      stack.append(...directLabels);
    }
  };

  const compactNavLabels = () => {
    const labels = new Map([
      ['/settings/metadata', 'Metadata'],
      ['/settings/external-search', 'Search'],
      ['/settings/scheduled-tasks', 'Scheduled Tasks'],
    ]);
    nav.querySelectorAll('a').forEach((link) => {
      const pathname = new URL(link.href, window.location.href).pathname;
      if (labels.has(pathname)) link.textContent = labels.get(pathname);
    });
  };

  balanceGeneral();
  compactNavLabels();
})();
