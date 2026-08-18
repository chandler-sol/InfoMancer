(() => {
  const coverLibrary = document.getElementById('cover-library');
  const libraryTable = document.querySelector('.library-table');
  if (!coverLibrary && !libraryTable) return;

  const inspector = document.getElementById('workspace-inspector');
  const itemFor = (target) => target?.closest?.('.cover-card, .library-title-row');
  const titleIdFor = (item) => item?.dataset?.workspaceTitleId || '';
  const inspectedItem = () => document.querySelector('.cover-card.workspace-selected, .library-title-row.workspace-selected');
  const inspectedTitleId = () => titleIdFor(inspectedItem());
  const interactive = (target) => target?.closest?.('input, button, summary, details, form, select, textarea, .item-action-menu, .cover-select-control');

  const selectedEntries = () => {
    const entries = new Map();
    document.querySelectorAll('.library-title-choice:checked').forEach((choice) => {
      const id = String(choice.value || '');
      if (!id || entries.has(id)) return;
      const item = choice.closest('.cover-card, .library-title-row')
        || document.querySelector(`[data-workspace-title-id="${CSS.escape(id)}"]`);
      const label = choice.dataset.title
        || item?.querySelector('.cover-card-link > strong, .title-link')?.textContent?.trim()
        || `Title ${id}`;
      entries.set(id, {id, label, item});
    });
    return [...entries.values()];
  };

  const setTitleChecked = (titleId, checked) => {
    const choices = [...document.querySelectorAll(`.library-title-choice[value="${CSS.escape(String(titleId))}"]`)];
    if (!choices.length) return;
    choices.forEach(choice => { choice.checked = checked; });
    choices[0].dispatchEvent(new Event('change', {bubbles: true}));
  };

  const itemForTitle = (titleId) => {
    const id = String(titleId);
    const visibleSelector = coverLibrary && !coverLibrary.hidden ? '.cover-card' : '.library-title-row';
    return [...document.querySelectorAll(visibleSelector)].find(item => titleIdFor(item) === id)
      || document.querySelector(`[data-workspace-title-id="${CSS.escape(id)}"]`);
  };

  let syntheticInspect = false;
  let inspectorDismissed = false;

  const inspectTitle = (titleId, {explicit = true} = {}) => {
    if (!explicit && inspectorDismissed) return;
    const item = itemForTitle(titleId);
    if (!item) return;
    if (explicit) inspectorDismissed = false;
    if (titleIdFor(item) === inspectedTitleId() && document.body.classList.contains('workspace-inspector-open')) return;
    const link = item.querySelector('.cover-card-link, .title-link');
    if (!link) return;
    syntheticInspect = true;
    link.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
    syntheticInspect = false;
  };

  const ensureInspectorSelectionBar = () => {
    if (!inspector) return null;
    let bar = inspector.querySelector('.library-inspector-selection-bar');
    if (bar) return bar;
    const head = inspector.querySelector('.workspace-inspector-head');
    if (!head) return null;

    bar = document.createElement('div');
    bar.className = 'library-inspector-selection-bar';

    const meta = document.createElement('span');
    meta.className = 'library-inspector-selection-count';

    const chooser = document.createElement('label');
    chooser.className = 'library-inspector-selection-chooser';
    const chooserLabel = document.createElement('span');
    chooserLabel.textContent = 'Inspecting';
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Choose a selected title to inspect');
    chooser.append(chooserLabel, select);

    const compare = document.createElement('button');
    compare.type = 'button';
    compare.className = 'button library-selection-compare';
    compare.textContent = 'Compare';

    select.addEventListener('change', () => inspectTitle(select.value, {explicit: true}));
    compare.addEventListener('click', () => openCompareDialog());

    bar.append(meta, chooser, compare);
    head.after(bar);
    return bar;
  };

  const factCache = new Map();
  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const addFact = (facts, label, value) => {
    const key = normalize(label).replace(/:$/, '');
    const clean = normalize(value);
    if (!key || !clean || facts.has(key)) return;
    facts.set(key, clean);
  };

  const fetchFacts = (titleId) => {
    const id = String(titleId);
    if (factCache.has(id)) return factCache.get(id);
    const request = fetch(`/library/inspector/${encodeURIComponent(id)}`, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {'X-Workspace-Inspector': '1'},
    }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const facts = new Map();

      doc.querySelectorAll('dt').forEach((dt) => {
        const dd = dt.nextElementSibling;
        if (dd?.tagName === 'DD') addFact(facts, dt.textContent, dd.textContent);
      });

      doc.querySelectorAll('.workspace-inspector-stat-grid > *, .workspace-inspector-media-grid > *').forEach((card) => {
        const label = card.querySelector('span, small')?.textContent;
        const value = card.querySelector('strong')?.textContent;
        addFact(facts, label, value);
      });

      return facts;
    }).catch(error => {
      factCache.delete(id);
      throw error;
    });
    factCache.set(id, request);
    return request;
  };

  const ensureCompareDialog = () => {
    let dialog = document.getElementById('library-selection-compare-dialog');
    if (dialog) return dialog;
    dialog = document.createElement('dialog');
    dialog.id = 'library-selection-compare-dialog';
    dialog.className = 'library-selection-compare-dialog';
    dialog.innerHTML = `
      <section class="library-compare-card">
        <header class="library-compare-head">
          <span><small>SELECTED TITLES</small><strong>Compare media</strong></span>
          <button type="button" data-library-compare-close aria-label="Close comparison">×</button>
        </header>
        <div class="library-compare-controls">
          <label><span>Left</span><select data-library-compare-left></select></label>
          <label><span>Right</span><select data-library-compare-right></select></label>
          <label class="library-compare-differences"><input type="checkbox" data-library-compare-differences> Differences only</label>
        </div>
        <div class="library-compare-body" data-library-compare-body></div>
      </section>`;
    document.body.append(dialog);
    dialog.querySelector('[data-library-compare-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    dialog.querySelector('[data-library-compare-left]').addEventListener('change', renderComparison);
    dialog.querySelector('[data-library-compare-right]').addEventListener('change', renderComparison);
    dialog.querySelector('[data-library-compare-differences]').addEventListener('change', renderComparison);
    return dialog;
  };

  const comparisonPriority = [
    'Resolution', 'Dynamic range', 'HDR', 'Video codec', 'Video', 'Bitrate',
    'Audio codec', 'Audio', 'Channels', 'Runtime', 'File size', 'Size',
    'Container', 'Files', 'Episodes', 'Missing',
  ];

  async function renderComparison() {
    const dialog = ensureCompareDialog();
    const left = dialog.querySelector('[data-library-compare-left]');
    const right = dialog.querySelector('[data-library-compare-right]');
    const body = dialog.querySelector('[data-library-compare-body]');
    const differencesOnly = dialog.querySelector('[data-library-compare-differences]').checked;
    if (!left.value || !right.value) return;
    if (left.value === right.value) {
      const alternate = [...right.options].find(option => option.value !== left.value);
      if (alternate) right.value = alternate.value;
    }

    body.innerHTML = '<p class="library-compare-state">Loading media details…</p>';
    try {
      const [leftFacts, rightFacts] = await Promise.all([fetchFacts(left.value), fetchFacts(right.value)]);
      const keys = [...new Set([...leftFacts.keys(), ...rightFacts.keys()])];
      const rank = key => {
        const normalized = key.toLowerCase();
        const index = comparisonPriority.findIndex(item => normalized.includes(item.toLowerCase()));
        return index < 0 ? 1000 : index;
      };
      keys.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));

      const table = document.createElement('div');
      table.className = 'library-compare-table';
      let shown = 0;
      keys.forEach((key) => {
        const leftValue = leftFacts.get(key) || '—';
        const rightValue = rightFacts.get(key) || '—';
        const same = leftValue === rightValue;
        if (differencesOnly && same) return;
        const row = document.createElement('div');
        row.className = `library-compare-row ${same ? 'same' : 'different'}`;
        const label = document.createElement('span');
        label.textContent = key;
        const leftCell = document.createElement('strong');
        leftCell.textContent = leftValue;
        const rightCell = document.createElement('strong');
        rightCell.textContent = rightValue;
        row.append(label, leftCell, rightCell);
        table.append(row);
        shown += 1;
      });
      if (!shown) {
        body.innerHTML = `<p class="library-compare-state">${differencesOnly ? 'No differences found in the available media facts.' : 'No comparable media facts were available for these titles.'}</p>`;
      } else {
        body.replaceChildren(table);
      }
    } catch (_error) {
      body.innerHTML = '<p class="library-compare-state error">Comparison details could not be loaded.</p>';
    }
  }

  function openCompareDialog() {
    const entries = selectedEntries();
    if (entries.length < 2) return;
    const dialog = ensureCompareDialog();
    const left = dialog.querySelector('[data-library-compare-left]');
    const right = dialog.querySelector('[data-library-compare-right]');
    const current = inspectedTitleId();

    const fill = (select) => {
      select.replaceChildren();
      entries.forEach(entry => {
        const option = document.createElement('option');
        option.value = entry.id;
        option.textContent = entry.label;
        select.append(option);
      });
    };
    fill(left);
    fill(right);
    left.value = entries.some(entry => entry.id === current) ? current : entries[0].id;
    right.value = entries.find(entry => entry.id !== left.value)?.id || entries[1].id;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    renderComparison();
  }

  let syncQueued = false;
  const queueSync = () => {
    if (syncQueued) return;
    syncQueued = true;
    window.setTimeout(() => {
      syncQueued = false;
      syncSelectionUI();
    }, 0);
  };

  const syncSelectionUI = () => {
    const entries = selectedEntries();
    const ids = new Set(entries.map(entry => entry.id));
    document.body.classList.toggle('library-has-selection', entries.length > 0);
    document.body.classList.toggle('library-multi-selection', entries.length > 1);

    const current = inspectedTitleId();
    if (entries.length === 0) {
      inspectorDismissed = false;
      const bar = inspector?.querySelector('.library-inspector-selection-bar');
      if (bar) bar.hidden = true;
      const dialog = document.getElementById('library-selection-compare-dialog');
      if (dialog?.open) dialog.close();
      return;
    }

    if ((!current || !ids.has(current)) && !inspectorDismissed) {
      inspectTitle(entries[0].id, {explicit: false});
    }

    const bar = ensureInspectorSelectionBar();
    if (!bar) return;
    bar.hidden = false;
    const count = bar.querySelector('.library-inspector-selection-count');
    const chooser = bar.querySelector('.library-inspector-selection-chooser');
    const select = chooser.querySelector('select');
    const compare = bar.querySelector('.library-selection-compare');
    count.textContent = `${entries.length} selected`;
    chooser.hidden = entries.length < 2;
    compare.hidden = entries.length < 2;

    select.replaceChildren();
    entries.forEach(entry => {
      const option = document.createElement('option');
      option.value = entry.id;
      option.textContent = entry.label;
      select.append(option);
    });
    const inspected = inspectedTitleId();
    if (inspected && ids.has(inspected)) select.value = inspected;
  };

  /* Keep cover checkbox clicks from falling through to the older card-click
     Inspector handler. The label still performs its native checkbox toggle. */
  coverLibrary?.addEventListener('click', event => {
    if (event.target.closest('.cover-select-control')) event.stopPropagation();
  });

  /* Normal card clicks own a single selection. Once two or more titles are selected,
     clicking an unselected card adds it to the working set, while clicking a selected
     card switches the Inspector to that title without disturbing the selection. */
  document.addEventListener('click', (event) => {
    if (syntheticInspect) return;
    const item = itemFor(event.target);
    if (!item || interactive(event.target)) return;
    const titleId = titleIdFor(item);
    if (!titleId) return;

    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      queueSync();
      return;
    }

    const entries = selectedEntries();
    const ids = new Set(entries.map(entry => entry.id));
    const isSelected = ids.has(titleId);
    const isCurrent = titleId === inspectedTitleId() && document.body.classList.contains('workspace-inspector-open');

    if (entries.length > 1 && !isSelected) {
      event.preventDefault();
      event.stopImmediatePropagation();
      setTitleChecked(titleId, true);
      queueSync();
      return;
    }

    /* A direct click on a title is an explicit request to inspect it. This is what
       reopens a drawer the user previously dismissed. */
    inspectorDismissed = false;

    if (isSelected && isCurrent) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }

    if (entries.length <= 1) {
      entries.forEach(entry => {
        if (entry.id !== titleId) setTitleChecked(entry.id, false);
      });
      if (!isSelected) setTitleChecked(titleId, true);
    }
    queueSync();
  }, true);

  document.addEventListener('change', (event) => {
    if (!event.target.matches('.library-title-choice')) return;
    queueSync();
  });

  document.addEventListener('click', (event) => {
    if (itemFor(event.target)) queueSync();
  });

  document.addEventListener('infomancer:library-results-updated', () => {
    factCache.clear();
    queueSync();
  });

  /* Closing the Inspector is an explicit user preference for the current selection.
     Keep the selection checked, but do not auto-open the drawer again until a title
     is deliberately clicked or chosen from the Inspector selector. */
  inspector?.querySelector('.workspace-inspector-close')?.addEventListener('click', () => {
    inspectorDismissed = true;
    queueSync();
  }, true);

  queueSync();
})();
