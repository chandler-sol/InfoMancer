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
  const metricDescriptions = {
    fresh: 'Refreshed within the last 30 days.',
    stale: 'Never refreshed, or last refreshed more than 30 days ago.',
    artwork: 'Titles that do not currently have saved poster artwork.',
    credits: 'Titles that do not currently have stored cast or crew credits.',
  };

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
  const metricByScope = new Map();
  metrics.slice(0, 4).forEach((metric, index) => {
    const scope = metricScopes[index];
    const description = metricDescriptions[scope];
    metricByScope.set(scope, metric);
    metric.classList.add('metadata-maintenance-metric');
    metric.dataset.metadataScope = scope;
    metric.tabIndex = 0;
    metric.setAttribute('role', 'button');
    metric.setAttribute(
      'aria-label',
      `${labels[scope]}. ${description} Activate to view matching titles.`
    );
    metric.title = `${description} Click to view matching titles.`;
    const hint = document.createElement('small');
    hint.className = 'metadata-maintenance-metric-hint';
    hint.textContent = `${description} View titles.`;
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
  const runningTitleIds = new Set();
  const PAGE_SIZE = 100;

  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const formatKind = (kind) => kind === 'tv' ? 'TV Show' : 'Movie';
  const phaseLabel = (phase = '') => ({
    basics: 'Checking title records',
    ratings: 'Updating ratings',
    episodes: 'Updating episode links',
    crew: 'Updating crew credits',
    principals: 'Updating cast credits',
    names: 'Resolving people',
  }[String(phase).toLowerCase()] || 'Refreshing metadata');

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

  const setInlineTask = (banner, tone, headingText, detailText = '') => {
    banner.hidden = false;
    banner.className = `metadata-maintenance-inline-task ${tone}`;
    banner.querySelector('strong').textContent = headingText;
    const detail = banner.querySelector('small');
    detail.textContent = detailText;
    detail.hidden = !detailText;
  };

  const updateMetricTotal = (scope, value) => {
    const metric = metricByScope.get(scope);
    const number = metric?.querySelector('strong');
    if (number && Number.isFinite(Number(value))) {
      number.textContent = Number(value).toLocaleString();
    }
  };

  const pollTitleRefresh = async (item, row, button, banner) => {
    try {
      for (let attempt = 0; attempt < 900; attempt += 1) {
        if (!row.isConnected) return;
        await sleep(attempt === 0 ? 450 : 1000);
        const response = await fetch(`/api/titles/${item.id}/metadata-refresh-state`, {
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const state = await response.json();
        const task = state.task || {};
        const queue = state.queue || {};

        if (['starting', 'running'].includes(String(task.status || ''))) {
          setInlineTask(
            banner,
            'working',
            phaseLabel(task.phase),
            'This title is refreshing here. It will not create a global notification.'
          );
          continue;
        }

        if (queue.status === 'failed' || state.metadata_refresh_error) {
          const error = queue.error || state.metadata_refresh_error || 'Metadata refresh failed.';
          setInlineTask(banner, 'error', 'Refresh could not finish', error);
          button.disabled = false;
          button.textContent = 'Retry';
          return;
        }

        if (queue.status === 'complete' || state.metadata_refreshed_at) {
          setInlineTask(banner, 'success', 'Refresh complete', 'Updating this list…');
          await sleep(650);
          const previousScroll = list.scrollTop;
          await loadScope(activeScope);
          list.scrollTop = Math.min(previousScroll, Math.max(0, list.scrollHeight - list.clientHeight));
          return;
        }
      }
      setInlineTask(
        banner,
        'error',
        'Refresh is taking longer than expected',
        'The background worker may still be running. Close and reopen this list to check its state.'
      );
      button.disabled = false;
      button.textContent = 'Check again';
    } catch (error) {
      setInlineTask(
        banner,
        'error',
        'Refresh status could not be checked',
        error?.message || 'Try again.'
      );
      button.disabled = false;
      button.textContent = 'Retry';
    } finally {
      runningTitleIds.delete(item.id);
    }
  };

  const refreshTitle = async (item, row, button, banner) => {
    if (runningTitleIds.has(item.id)) return;
    runningTitleIds.add(item.id);
    button.disabled = true;
    button.textContent = 'Starting…';
    setInlineTask(banner, 'working', 'Starting refresh', 'Preparing this title for metadata refresh.');
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
      button.textContent = 'Refreshing…';
      setInlineTask(
        banner,
        'working',
        'Metadata refresh queued',
        'Progress will stay with this title instead of the notification widget.'
      );
      await pollTitleRefresh(item, row, button, banner);
    } catch (error) {
      runningTitleIds.delete(item.id);
      button.textContent = 'Retry';
      button.disabled = false;
      setInlineTask(
        banner,
        'error',
        'Refresh could not start',
        error?.message || 'Try again.'
      );
    }
  };

  const renderItem = (item) => {
    const row = document.createElement('article');
    row.className = 'metadata-maintenance-row';
    row.dataset.titleId = String(item.id);

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

    const banner = document.createElement('div');
    banner.className = 'metadata-maintenance-inline-task';
    banner.hidden = true;
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    const indicator = document.createElement('span');
    indicator.className = 'metadata-maintenance-inline-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    const bannerCopy = document.createElement('span');
    const bannerHeading = document.createElement('strong');
    const bannerDetail = document.createElement('small');
    bannerCopy.append(bannerHeading, bannerDetail);
    banner.append(indicator, bannerCopy);

    action.addEventListener('click', () => refreshTitle(item, row, action, banner));
    row.append(copy, action, banner);
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
      updateMetricTotal(activeScope, total);
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
