(() => {
  if (window.location.pathname !== '/settings/metadata') return;

  const csrfToken = document.body?.dataset.csrfToken || '';

  const setupTvdbCredentials = () => {
    const manageLink = document.querySelector('a[href="/getting-started/metadata"]');
    const providerCard = manageLink?.closest('.settings-card');
    if (!manageLink || !providerCard) return;

    manageLink.setAttribute('aria-haspopup', 'dialog');
    manageLink.setAttribute('aria-controls', 'tvdb-credentials-dialog');

    const dialog = document.createElement('dialog');
    dialog.id = 'tvdb-credentials-dialog';
    dialog.className = 'tvdb-credentials-dialog';
    dialog.setAttribute('aria-labelledby', 'tvdb-credentials-title');

    const shell = document.createElement('div');
    shell.className = 'tvdb-credentials-shell';

    const header = document.createElement('header');
    header.className = 'tvdb-credentials-head';
    const headingCopy = document.createElement('div');
    const eyebrow = document.createElement('p');
    eyebrow.className = 'eyebrow';
    eyebrow.textContent = 'TVDB';
    const heading = document.createElement('h2');
    heading.id = 'tvdb-credentials-title';
    heading.textContent = 'Manage credentials';
    const intro = document.createElement('p');
    intro.className = 'muted';
    intro.textContent = 'Update the credentials this InfoMancer installation uses for TVDB metadata. Existing secrets are never shown back in the browser.';
    headingCopy.append(eyebrow, heading, intro);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'tvdb-credentials-close';
    close.setAttribute('aria-label', 'Close TVDB credentials');
    close.textContent = '×';
    header.append(headingCopy, close);

    const form = document.createElement('form');
    form.className = 'tvdb-credentials-form';
    form.method = 'post';
    form.action = '/settings/metadata/tvdb-credentials';

    const keyLabel = document.createElement('label');
    keyLabel.textContent = 'Project API key';
    const keyInput = document.createElement('input');
    keyInput.type = 'password';
    keyInput.name = 'api_key';
    keyInput.autocomplete = 'off';
    keyInput.placeholder = 'Leave blank to keep the connected key';
    const keyHelp = document.createElement('small');
    keyHelp.textContent = 'Enter a new key only when you want to replace the one already configured.';
    keyLabel.append(keyInput, keyHelp);

    const pinLabel = document.createElement('label');
    pinLabel.textContent = 'Subscriber PIN';
    const pinInput = document.createElement('input');
    pinInput.type = 'password';
    pinInput.name = 'subscriber_pin';
    pinInput.autocomplete = 'off';
    pinInput.placeholder = 'Leave blank to keep the saved PIN';
    const pinHelp = document.createElement('small');
    pinHelp.textContent = 'Only enter a PIN when your TVDB access model requires one.';
    pinLabel.append(pinInput, pinHelp);

    const notice = document.createElement('div');
    notice.className = 'tvdb-credentials-notice';
    notice.setAttribute('role', 'status');
    notice.hidden = true;

    const footer = document.createElement('footer');
    footer.className = 'tvdb-credentials-footer';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'button';
    cancel.textContent = 'Cancel';
    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'button primary';
    save.textContent = 'Verify and save';
    footer.append(cancel, save);

    form.append(keyLabel, pinLabel, notice, footer);
    shell.append(header, form);
    dialog.append(shell);
    document.body.append(dialog);

    const setNotice = (message = '', tone = '') => {
      notice.hidden = !message;
      notice.className = `tvdb-credentials-notice${tone ? ` ${tone}` : ''}`;
      notice.textContent = message;
    };

    const updateProviderCard = (data) => {
      const facts = [...providerCard.querySelectorAll('.settings-facts dd')];
      if (facts[0] && data.key_hint) facts[0].textContent = data.key_hint;
      if (facts[1]) facts[1].textContent = data.pin_configured ? 'Configured' : 'Not configured';
      const state = providerCard.querySelector('.settings-state');
      if (state) {
        state.classList.remove('warn');
        state.classList.add('good');
        state.textContent = 'Configured';
      }
      providerCard.querySelector('form[action="/settings/metadata/tvdb-test"] button')?.removeAttribute('disabled');
    };

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      setNotice();
      save.disabled = true;
      save.textContent = 'Verifying…';
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
            'X-InfoMancer-Async': '1',
            ...(csrfToken ? {'X-CSRF-Token': csrfToken} : {}),
          },
          body: new FormData(form),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        updateProviderCard(data);
        keyInput.value = '';
        pinInput.value = '';
        setNotice(data.detail || 'TVDB credentials verified and saved securely.', 'success');
      } catch (error) {
        setNotice(error?.message || 'TVDB credentials could not be saved.', 'error');
      } finally {
        save.disabled = false;
        save.textContent = 'Verify and save';
      }
    });

    const closeDialog = () => dialog.close();
    manageLink.addEventListener('click', (event) => {
      event.preventDefault();
      setNotice();
      dialog.showModal();
      window.setTimeout(() => keyInput.focus(), 0);
    });
    close.addEventListener('click', closeDialog);
    cancel.addEventListener('click', closeDialog);
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) closeDialog();
    });
  };

  setupTvdbCredentials();

  const staleScopeInput = document.querySelector(
    'form[action="/metadata/queue"] input[name="scope"][value="stale"]'
  );
  const staleForm = staleScopeInput?.form;
  const card = staleForm?.closest('.settings-card');
  if (!card) return;

  const retryForm = card.querySelector('form[action="/metadata/retry-failed"]');
  const metrics = [...card.querySelectorAll('.settings-metrics > div')];
  if (metrics.length < 4) return;

  const scopes = [
    {key: 'fresh', label: 'Fresh'},
    {key: 'stale', label: 'Stale'},
    {key: 'artwork', label: 'Missing artwork'},
    {key: 'credits', label: 'Missing credits'},
    {key: 'failures', label: 'Failures'},
  ];
  const labels = Object.fromEntries(scopes.map((scope) => [scope.key, scope.label]));

  card.classList.add('metadata-maintenance-card');

  const staleButton = staleForm.querySelector('button');
  if (staleButton) staleButton.textContent = 'Refresh all stale';

  const oldTable = card.querySelector('.settings-table-wrap');
  oldTable?.remove();

  const actions = staleForm.closest('.actions');
  if (actions) actions.classList.add('metadata-maintenance-actions');

  const viewButton = document.createElement('button');
  viewButton.type = 'button';
  viewButton.className = 'button metadata-maintenance-view';
  viewButton.textContent = 'View titles';
  actions?.append(viewButton);

  const metricScopes = ['fresh', 'stale', 'artwork', 'credits'];
  metrics.slice(0, 4).forEach((metric, index) => {
    const scope = metricScopes[index];
    metric.classList.add('metadata-maintenance-metric');
    metric.dataset.metadataScope = scope;
    metric.tabIndex = 0;
    metric.setAttribute('role', 'button');
    metric.setAttribute('aria-label', `View ${labels[scope].toLowerCase()} titles`);
    const hint = document.createElement('small');
    hint.className = 'metadata-maintenance-metric-hint';
    hint.textContent = 'View titles';
    metric.append(hint);
  });

  const dialog = document.createElement('dialog');
  dialog.className = 'metadata-maintenance-dialog';
  dialog.setAttribute('aria-labelledby', 'metadata-maintenance-dialog-title');

  const shell = document.createElement('div');
  shell.className = 'metadata-maintenance-dialog-shell';

  const heading = document.createElement('header');
  heading.className = 'metadata-maintenance-dialog-head';
  const headingCopy = document.createElement('div');
  const eyebrow = document.createElement('p');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = 'METADATA MAINTENANCE';
  const title = document.createElement('h2');
  title.id = 'metadata-maintenance-dialog-title';
  title.textContent = 'Library metadata';
  const subtitle = document.createElement('p');
  subtitle.className = 'muted';
  subtitle.textContent = 'Review titles by maintenance state and refresh individual records.';
  headingCopy.append(eyebrow, title, subtitle);
  const closeButton = document.createElement('button');
  closeButton.type = 'button';
  closeButton.className = 'metadata-maintenance-close';
  closeButton.setAttribute('aria-label', 'Close metadata maintenance');
  closeButton.textContent = '×';
  heading.append(headingCopy, closeButton);

  const scopeBar = document.createElement('nav');
  scopeBar.className = 'metadata-maintenance-scopes';
  scopeBar.setAttribute('aria-label', 'Metadata maintenance views');
  const scopeButtons = new Map();
  scopes.forEach((scope) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.scope = scope.key;
    button.textContent = scope.label;
    scopeButtons.set(scope.key, button);
    scopeBar.append(button);
  });

  const summary = document.createElement('div');
  summary.className = 'metadata-maintenance-summary';
  const summaryText = document.createElement('span');
  const bulkAction = document.createElement('button');
  bulkAction.type = 'button';
  bulkAction.className = 'button metadata-maintenance-bulk';
  bulkAction.hidden = true;
  summary.append(summaryText, bulkAction);

  const list = document.createElement('div');
  list.className = 'metadata-maintenance-list';
  list.setAttribute('aria-live', 'polite');

  const footer = document.createElement('footer');
  footer.className = 'metadata-maintenance-dialog-footer';
  const loadMore = document.createElement('button');
  loadMore.type = 'button';
  loadMore.className = 'button';
  loadMore.textContent = 'Load more';
  loadMore.hidden = true;
  const closeFooter = document.createElement('button');
  closeFooter.type = 'button';
  closeFooter.className = 'button';
  closeFooter.textContent = 'Close';
  footer.append(loadMore, closeFooter);

  shell.append(heading, scopeBar, summary, list, footer);
  dialog.append(shell);
  document.body.append(dialog);

  let activeScope = 'stale';
  let offset = 0;
  let total = 0;
  let loading = false;
  let requestController = null;
  const PAGE_SIZE = 100;

  const formatKind = (kind) => kind === 'tv' ? 'TV Show' : 'Movie';

  const issueChip = (text, tone = '') => {
    const chip = document.createElement('span');
    chip.className = `metadata-maintenance-chip${tone ? ` ${tone}` : ''}`;
    chip.textContent = text;
    return chip;
  };

  const statusChips = (item) => {
    const chips = [];
    if (item.error) chips.push(issueChip('Failed', 'danger'));
    if (item.stale) chips.push(issueChip('Stale', 'warn'));
    else chips.push(issueChip('Fresh', 'good'));
    if (item.artwork_missing) chips.push(issueChip('Artwork missing'));
    if (item.credits_missing) chips.push(issueChip('Credits missing'));
    return chips;
  };

  const refreshTitle = async (item, button) => {
    button.disabled = true;
    button.textContent = 'Starting…';
    try {
      const response = await fetch(`/titles/${item.id}/imdb-refresh`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-InfoMancer-Async': '1',
          ...(csrfToken ? {'X-CSRF-Token': csrfToken} : {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      button.textContent = data.started ? 'Queued' : 'Refresh';
      if (!data.started) button.disabled = false;
    } catch (error) {
      button.textContent = 'Refresh';
      button.disabled = false;
      const message = document.createElement('small');
      message.className = 'metadata-maintenance-row-error';
      message.textContent = error?.message || 'Refresh could not be started.';
      button.closest('.metadata-maintenance-row')?.querySelector('.metadata-maintenance-row-copy')?.append(message);
    }
  };

  const renderItem = (item) => {
    const row = document.createElement('article');
    row.className = 'metadata-maintenance-row';

    const copy = document.createElement('div');
    copy.className = 'metadata-maintenance-row-copy';
    const name = document.createElement('a');
    name.href = `/titles/${item.id}`;
    name.className = 'metadata-maintenance-row-title';
    name.textContent = item.title;
    const meta = document.createElement('span');
    meta.className = 'metadata-maintenance-row-meta';
    meta.textContent = [formatKind(item.kind), item.year || null, item.provider || null]
      .filter(Boolean).join(' · ');
    const chips = document.createElement('div');
    chips.className = 'metadata-maintenance-row-chips';
    chips.append(...statusChips(item));
    copy.append(name, meta, chips);
    if (item.error) {
      const error = document.createElement('small');
      error.className = 'metadata-maintenance-row-error';
      error.textContent = item.error;
      copy.append(error);
    }

    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'button metadata-maintenance-row-refresh';
    action.textContent = 'Refresh';
    action.addEventListener('click', () => refreshTitle(item, action));

    row.append(copy, action);
    return row;
  };

  const setLoading = (value) => {
    loading = value;
    loadMore.disabled = value;
    scopeButtons.forEach((button) => { button.disabled = value; });
  };

  const updateBulkAction = () => {
    bulkAction.hidden = false;
    if (activeScope === 'stale') {
      bulkAction.textContent = 'Refresh all stale';
      bulkAction.disabled = Boolean(staleButton?.disabled);
      bulkAction.onclick = () => staleForm.requestSubmit();
    } else if (activeScope === 'failures') {
      bulkAction.textContent = 'Retry failures';
      bulkAction.disabled = Boolean(retryForm?.querySelector('button')?.disabled);
      bulkAction.onclick = () => retryForm?.requestSubmit();
    } else {
      bulkAction.hidden = true;
      bulkAction.onclick = null;
    }
  };

  const loadScope = async (scope, {append = false} = {}) => {
    if (loading) requestController?.abort();
    requestController = new AbortController();
    if (!append) {
      activeScope = scopes.some((item) => item.key === scope) ? scope : 'stale';
      offset = 0;
      total = 0;
      list.replaceChildren();
    }
    scopeButtons.forEach((button, key) => {
      const active = key === activeScope;
      button.classList.toggle('active', active);
      button.setAttribute('aria-current', active ? 'true' : 'false');
    });
    updateBulkAction();
    setLoading(true);
    if (!append) {
      const loadingRow = document.createElement('div');
      loadingRow.className = 'metadata-maintenance-loading';
      loadingRow.textContent = 'Loading titles…';
      list.append(loadingRow);
    }

    try {
      const url = new URL('/api/metadata/maintenance', window.location.origin);
      url.searchParams.set('scope', activeScope);
      url.searchParams.set('limit', String(PAGE_SIZE));
      url.searchParams.set('offset', String(offset));
      const response = await fetch(url, {
        credentials: 'same-origin',
        cache: 'no-store',
        signal: requestController.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      total = Number(data.total || 0);
      if (!append) list.replaceChildren();
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length && offset === 0) {
        const empty = document.createElement('div');
        empty.className = 'metadata-maintenance-empty';
        empty.textContent = `No ${labels[activeScope].toLowerCase()} titles.`;
        list.append(empty);
      } else {
        list.append(...items.map(renderItem));
      }
      offset += items.length;
      summaryText.textContent = `${total.toLocaleString()} ${labels[activeScope].toLowerCase()} title${total === 1 ? '' : 's'}`;
      loadMore.hidden = offset >= total;
    } catch (error) {
      if (error?.name === 'AbortError') return;
      if (!append) list.replaceChildren();
      const failure = document.createElement('div');
      failure.className = 'metadata-maintenance-empty error';
      failure.textContent = 'Title details could not be loaded. Try again.';
      list.append(failure);
      loadMore.hidden = true;
    } finally {
      setLoading(false);
    }
  };

  const openDialog = (scope = 'stale') => {
    dialog.showModal();
    loadScope(scope);
  };

  metrics.slice(0, 4).forEach((metric) => {
    const activate = () => openDialog(metric.dataset.metadataScope || 'stale');
    metric.addEventListener('click', activate);
    metric.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      activate();
    });
  });
  viewButton.addEventListener('click', () => openDialog('stale'));
  scopeButtons.forEach((button, scope) => button.addEventListener('click', () => loadScope(scope)));
  loadMore.addEventListener('click', () => loadScope(activeScope, {append: true}));
  closeButton.addEventListener('click', () => dialog.close());
  closeFooter.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
