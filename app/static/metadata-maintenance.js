(() => {
  if (window.location.pathname !== '/settings/metadata') return;

  const csrfToken = document.body?.dataset.csrfToken || '';

  const asyncHeaders = () => ({
    'Accept': 'application/json',
    'X-InfoMancer-Async': '1',
    ...(csrfToken ? {'X-CSRF-Token': csrfToken} : {}),
  });

  const fetchWithTimeout = async (url, options = {}, timeoutMs = 10000) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, {...options, signal: controller.signal});
    } finally {
      window.clearTimeout(timer);
    }
  };

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
      providerCard
        .querySelector('form[action="/settings/metadata/tvdb-test"] button')
        ?.removeAttribute('disabled');
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
          headers: asyncHeaders(),
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
    fresh: 'Matched titles refreshed within the last 30 days.',
    stale: 'Matched titles never refreshed, or last refreshed more than 30 days ago.',
    artwork: 'Matched titles that do not currently have saved poster artwork.',
    credits: 'Matched titles that do not currently have stored cast or crew credits.',
  };

  card.classList.add('metadata-maintenance-card');
  const staleButton = staleForm.querySelector('button');
  const retryButton = retryForm?.querySelector('button');
  if (staleButton) staleButton.textContent = 'Refresh all stale';

  card.querySelector('.settings-table-wrap')?.remove();

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
    const hint = document.createElement('small');
    hint.className = 'metadata-maintenance-metric-hint';
    hint.textContent = description;
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
  subtitle.textContent = 'Review matched titles by maintenance state and refresh individual records.';
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

  const PAGE_SIZE = 100;
  const SUCCESS_LINGER_MS = 1250;
  let activeScope = 'stale';
  const scopeCache = new Map(
    scopes.map(({key}) => [key, {
      items: [],
      total: 0,
      offset: 0,
      loaded: false,
      done: false,
      promise: null,
      scrollTop: 0,
    }])
  );
  const refreshJobs = new Map();

  const sleep = (milliseconds) =>
    new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  const formatKind = (kind) => kind === 'tv' ? 'TV Show' : 'Movie';

  const phaseLabel = (phase = '') => ({
    provider: 'Contacting TVDB',
    details: 'Updating title details',
    credits: 'Updating credits',
    save: 'Saving metadata',
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

  const updateMetricTotal = (scope, value) => {
    const numericValue = Number(value);
    const number = metricByScope.get(scope)?.querySelector('strong');
    if (number && Number.isFinite(numericValue)) {
      number.textContent = numericValue.toLocaleString();
    }
    if (scope === 'stale' && staleButton && Number.isFinite(numericValue)) {
      staleButton.disabled = numericValue === 0;
    }
  };

  const syncMetricTotals = () => {
    metricScopes.forEach(async (scope) => {
      try {
        const url = new URL('/api/metadata/maintenance', window.location.origin);
        url.searchParams.set('scope', scope);
        url.searchParams.set('limit', '1');
        const response = await fetch(url, {
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) return;
        const data = await response.json();
        updateMetricTotal(scope, Number(data.total || 0));
      } catch (_error) {
        // Keep the server-rendered value if the lightweight sync cannot complete.
      }
    });
  };

  const applyJobToRow = (row, job) => {
    const button = row.querySelector('.metadata-maintenance-row-refresh');
    const banner = row.querySelector('.metadata-maintenance-inline-task');
    if (!button || !banner) return;

    if (!job || job.status === 'idle') {
      banner.hidden = true;
      button.disabled = false;
      button.textContent = 'Refresh';
      return;
    }

    banner.hidden = false;
    banner.className = `metadata-maintenance-inline-task ${job.tone || 'working'}`;
    banner.querySelector('strong').textContent = job.heading || 'Refreshing metadata';
    const detail = banner.querySelector('small');
    detail.textContent = job.detail || '';
    detail.hidden = !job.detail;

    const busy = ['starting', 'running'].includes(job.status);
    button.disabled = busy;
    button.textContent = busy ? 'Refreshing…' : job.status === 'failed' ? 'Retry' : job.status === 'complete' ? 'Done' : 'Refresh';
  };

  const updateVisibleJob = (titleId) => {
    const row = list.querySelector(`.metadata-maintenance-row[data-title-id="${String(titleId)}"]`);
    if (row) applyJobToRow(row, refreshJobs.get(titleId));
  };

  const setJob = (titleId, patch) => {
    const current = refreshJobs.get(titleId) || {
      status: 'idle',
      tone: 'working',
      heading: '',
      detail: '',
      polling: false,
    };
    const next = {...current, ...patch};
    refreshJobs.set(titleId, next);
    updateVisibleJob(titleId);
    return next;
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

    action.addEventListener('click', () => refreshTitle(item));
    row.append(copy, action, banner);
    applyJobToRow(row, refreshJobs.get(item.id));
    return row;
  };

  const setBulkButtonsBusy = (scope, busy) => {
    const sourceButton = scope === 'stale' ? staleButton : retryButton;
    if (sourceButton) sourceButton.disabled = busy;
    if (activeScope === scope) bulkAction.disabled = busy;
  };

  const startBulkRefresh = async (scope) => {
    if (!['stale', 'failures'].includes(scope)) return;
    setBulkButtonsBusy(scope, true);
    try {
      const url = new URL('/api/metadata/maintenance/bulk-refresh', window.location.origin);
      url.searchParams.set('scope', scope);
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: asyncHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      summaryText.textContent = data.detail || 'Metadata refresh queued.';
      if (!data.started) setBulkButtonsBusy(scope, false);
    } catch (error) {
      summaryText.textContent = error?.message || 'Metadata refresh could not be started.';
      setBulkButtonsBusy(scope, false);
    }
  };

  const updateBulkAction = () => {
    bulkAction.hidden = false;
    if (activeScope === 'stale') {
      bulkAction.textContent = 'Refresh all stale';
      bulkAction.disabled = Boolean(staleButton?.disabled);
      bulkAction.onclick = () => startBulkRefresh('stale');
    } else if (activeScope === 'failures') {
      bulkAction.textContent = 'Retry failures';
      bulkAction.disabled = Boolean(retryButton?.disabled);
      bulkAction.onclick = () => startBulkRefresh('failures');
    } else {
      bulkAction.hidden = true;
      bulkAction.onclick = null;
    }
  };

  const renderActiveScope = () => {
    const state = scopeCache.get(activeScope);
    if (!state) return;

    list.replaceChildren();
    if (!state.loaded) {
      const loadingRow = document.createElement('div');
      loadingRow.className = 'metadata-maintenance-loading';
      loadingRow.textContent = `Loading ${labels[activeScope].toLowerCase()} titles…`;
      list.append(loadingRow);
      summaryText.textContent = `Loading ${labels[activeScope].toLowerCase()} titles…`;
      loadMore.hidden = true;
      return;
    }

    if (!state.items.length) {
      const empty = document.createElement('div');
      empty.className = 'metadata-maintenance-empty';
      empty.textContent = `No ${labels[activeScope].toLowerCase()} titles.`;
      list.append(empty);
    } else {
      list.append(...state.items.map(renderItem));
    }

    summaryText.textContent = `${state.total.toLocaleString()} ${labels[activeScope].toLowerCase()} title${state.total === 1 ? '' : 's'}`;
    updateMetricTotal(activeScope, state.total);
    loadMore.hidden = state.done;
    requestAnimationFrame(() => {
      list.scrollTop = state.scrollTop || 0;
    });
  };

  const fetchScope = async (scope, {append = false, force = false} = {}) => {
    const state = scopeCache.get(scope);
    if (!state) return;
    if (state.promise && !force) return state.promise;
    if (state.loaded && !append && !force) return state;
    if (append && state.done) return state;

    const offset = append ? state.offset : 0;
    state.promise = (async () => {
      try {
        const url = new URL('/api/metadata/maintenance', window.location.origin);
        url.searchParams.set('scope', scope);
        url.searchParams.set('limit', String(PAGE_SIZE));
        url.searchParams.set('offset', String(offset));

        const response = await fetch(url, {
          credentials: 'same-origin',
          cache: 'no-store',
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const items = Array.isArray(data.items) ? data.items : [];
        const total = Number(data.total || 0);

        if (append) state.items.push(...items);
        else state.items = items;
        state.total = total;
        state.offset = offset + items.length;
        state.loaded = true;
        state.done = state.offset >= total;
        state.error = '';

        if (scope === activeScope) renderActiveScope();
        updateMetricTotal(scope, total);
        return state;
      } catch (error) {
        state.error = error?.message || 'Could not load titles.';
        if (scope === activeScope) {
          list.replaceChildren();
          const failure = document.createElement('div');
          failure.className = 'metadata-maintenance-empty error';
          failure.textContent = 'Title details could not be loaded. Try again.';
          list.append(failure);
          summaryText.textContent = `${labels[scope]} could not be loaded`;
          loadMore.hidden = true;
        }
        return state;
      } finally {
        state.promise = null;
      }
    })();

    return state.promise;
  };

  const invalidateScopes = () => {
    scopeCache.forEach((state) => {
      state.loaded = false;
      state.done = false;
      state.items = [];
      state.offset = 0;
      state.total = 0;
      state.promise = null;
      state.scrollTop = 0;
    });
  };

  const prefetchOtherScopes = () => {
    scopes
      .map(({key}) => key)
      .filter((scope) => scope !== activeScope)
      .forEach((scope) => window.setTimeout(() => fetchScope(scope), 0));
  };

  const pollTitleRefresh = async (item) => {
    const job = refreshJobs.get(item.id);
    if (!job || job.polling) return;

    setJob(item.id, {polling: true});
    try {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await sleep(attempt === 0 ? 450 : 1000);
        const response = await fetchWithTimeout(
          `/api/titles/${item.id}/metadata-refresh-state`,
          {credentials: 'same-origin', cache: 'no-store'},
          10000,
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const state = await response.json();
        const task = state.task || {};
        const queue = state.queue || {};

        if (['starting', 'running'].includes(String(task.status || ''))) {
          setJob(item.id, {
            status: 'running',
            tone: 'working',
            heading: phaseLabel(task.phase),
            detail: 'This refresh stays with the title and does not create a global notification.',
          });
          continue;
        }

        if (queue.status === 'failed' || state.metadata_refresh_error) {
          setJob(item.id, {
            status: 'failed',
            tone: 'error',
            heading: 'Refresh could not finish',
            detail: queue.error || state.metadata_refresh_error || 'Metadata refresh failed.',
            polling: false,
          });
          return;
        }

        if (queue.status === 'complete' || state.metadata_refreshed_at) {
          setJob(item.id, {
            status: 'complete',
            tone: 'success',
            heading: 'Refresh complete',
            detail: 'Updating maintenance views…',
            polling: false,
          });
          await sleep(SUCCESS_LINGER_MS);
          invalidateScopes();
          await fetchScope(activeScope, {force: true});
          prefetchOtherScopes();
          return;
        }
      }

      setJob(item.id, {
        status: 'failed',
        tone: 'error',
        heading: 'Refresh timed out',
        detail: 'No final status arrived within two minutes. The refresh was stopped from spinning indefinitely.',
        polling: false,
      });
    } catch (error) {
      const timedOut = error?.name === 'AbortError';
      setJob(item.id, {
        status: 'failed',
        tone: 'error',
        heading: timedOut ? 'Refresh status timed out' : 'Refresh status could not be checked',
        detail: timedOut ? 'InfoMancer did not return refresh status within 10 seconds.' : (error?.message || 'Try again.'),
        polling: false,
      });
    }
  };

  const refreshTitle = async (item) => {
    const existing = refreshJobs.get(item.id);
    if (existing && ['starting', 'running'].includes(existing.status)) return;

    setJob(item.id, {
      status: 'starting',
      tone: 'working',
      heading: 'Refreshing metadata',
      detail: 'Contacting TVDB for this title.',
      polling: false,
    });

    try {
      const response = await fetchWithTimeout(
        `/titles/${item.id}/imdb-refresh`,
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: asyncHeaders(),
        },
        95000,
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

      if (data.completed === true || data.status === 'complete') {
        setJob(item.id, {
          status: 'complete',
          tone: 'success',
          heading: 'Refresh complete',
          detail: data.duration_ms
            ? `TVDB refresh finished in ${(Number(data.duration_ms) / 1000).toFixed(1)} seconds.`
            : 'Metadata is up to date.',
          polling: false,
        });
        await sleep(SUCCESS_LINGER_MS);
        invalidateScopes();
        await fetchScope(activeScope, {force: true});
        prefetchOtherScopes();
        return;
      }

      setJob(item.id, {
        status: 'running',
        tone: 'working',
        heading: 'Metadata refresh started',
        detail: 'Waiting for the server to report a final result.',
      });
      pollTitleRefresh(item);
    } catch (error) {
      const timedOut = error?.name === 'AbortError';
      setJob(item.id, {
        status: 'failed',
        tone: 'error',
        heading: timedOut ? 'Refresh timed out' : 'Refresh failed',
        detail: timedOut
          ? 'TVDB did not finish this title within 95 seconds. Try again or test the TVDB connection in Metadata Settings.'
          : (error?.message || 'Try again.'),
        polling: false,
      });
    }
  };

  const switchScope = (scope) => {
    if (!scopeCache.has(scope) || scope === activeScope) return;

    const current = scopeCache.get(activeScope);
    if (current) current.scrollTop = list.scrollTop;

    activeScope = scope;
    scopeButtons.forEach((button, key) => {
      const active = key === activeScope;
      button.classList.toggle('active', active);
      button.setAttribute('aria-current', active ? 'true' : 'false');
    });
    updateBulkAction();

    const state = scopeCache.get(activeScope);
    renderActiveScope();
    if (!state.loaded) fetchScope(activeScope);
  };

  const openDialog = (scope = 'stale') => {
    if (!scopeCache.has(scope)) scope = 'stale';
    activeScope = scope;
    scopeButtons.forEach((button, key) => {
      const active = key === activeScope;
      button.classList.toggle('active', active);
      button.setAttribute('aria-current', active ? 'true' : 'false');
    });
    updateBulkAction();
    dialog.showModal();

    const state = scopeCache.get(activeScope);
    renderActiveScope();
    if (!state.loaded) fetchScope(activeScope).then(prefetchOtherScopes);
    else prefetchOtherScopes();
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

  staleForm.addEventListener('submit', (event) => {
    event.preventDefault();
    startBulkRefresh('stale');
  });
  retryForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    startBulkRefresh('failures');
  });
  viewButton.addEventListener('click', () => openDialog('stale'));
  scopeButtons.forEach((button, scope) => {
    button.addEventListener('click', () => switchScope(scope));
  });
  loadMore.addEventListener('click', async () => {
    const state = scopeCache.get(activeScope);
    if (!state || state.done || state.promise) return;
    loadMore.disabled = true;
    await fetchScope(activeScope, {append: true});
    loadMore.disabled = false;
  });
  closeButton.addEventListener('click', () => dialog.close());
  closeFooter.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });

  // The server-rendered Settings context predates the matched-only maintenance
  // boundary. Correct the four visible totals immediately from the canonical
  // maintenance API without loading the full modal lists.
  syncMetricTotals();
})();
