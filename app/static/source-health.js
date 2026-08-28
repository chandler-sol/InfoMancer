(() => {
  const sourceRows = [...document.querySelectorAll('.root-row')];
  if (!sourceRows.length || typeof window.fetch !== 'function') return;

  const dialog = document.createElement('dialog');
  dialog.className = 'source-health-dialog';
  dialog.setAttribute('aria-labelledby', 'source-health-title');
  dialog.innerHTML = `
    <div class="source-health-dialog-shell">
      <header class="source-health-dialog-head">
        <div>
          <p class="eyebrow">SOURCE HEALTH</p>
          <h2 id="source-health-title">Source health</h2>
          <p class="muted" data-source-health-path></p>
        </div>
        <button class="dialog-icon-close" type="button" data-source-health-close aria-label="Close source health details"></button>
      </header>
      <div class="source-health-dialog-body" data-source-health-body>
        <p class="muted">Loading source health details…</p>
      </div>
      <footer class="source-health-dialog-actions">
        <button class="button" type="button" data-source-health-rescan>Rescan source</button>
        <a class="button" data-source-health-logs href="/logs">Related logs</a>
        <a class="button primary" data-source-health-review href="/review?bucket=sources">View in Review</a>
      </footer>
    </div>`;
  document.body.append(dialog);

  const title = dialog.querySelector('#source-health-title');
  const path = dialog.querySelector('[data-source-health-path]');
  const body = dialog.querySelector('[data-source-health-body]');
  const reviewLink = dialog.querySelector('[data-source-health-review]');
  const logsLink = dialog.querySelector('[data-source-health-logs]');
  const rescan = dialog.querySelector('[data-source-health-rescan]');
  let activeRow = null;

  const close = () => {
    if (dialog.open) dialog.close();
  };
  dialog.querySelector('[data-source-health-close]')?.addEventListener('click', close);
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) close();
  });

  const addDetail = (container, label, value) => {
    if (value === null || value === undefined || String(value).trim() === '') return;
    const row = document.createElement('div');
    row.className = 'source-health-fact';
    const key = document.createElement('span');
    key.textContent = label;
    const content = document.createElement('strong');
    content.textContent = String(value);
    row.append(key, content);
    container.append(row);
  };

  const render = (payload) => {
    const root = payload?.root || {};
    title.textContent = root.label || 'Source health';
    path.textContent = root.path || '';
    reviewLink.href = payload.review_url || '/review?bucket=sources';
    logsLink.href = payload.logs_url || '/logs';
    body.replaceChildren();

    const statusLine = document.createElement('div');
    statusLine.className = `source-health-modal-status source-health-${root.status || 'unknown'}`;
    const count = Number(payload.issue_count || 0);
    statusLine.textContent = `${String(root.status || 'unknown').toUpperCase()}${count ? ` · ${count} issue${count === 1 ? '' : 's'}` : ''}`;
    body.append(statusLine);

    const explanation = document.createElement('section');
    explanation.className = 'source-health-section';
    const what = document.createElement('h3');
    what.textContent = 'What happened';
    const summary = document.createElement('strong');
    summary.textContent = payload.summary || 'InfoMancer recorded a source-health issue.';
    const copy = document.createElement('p');
    copy.textContent = payload.explanation || payload.technical_detail || 'No additional explanation was recorded.';
    explanation.append(what, summary, copy);
    if (payload.technical_detail && payload.technical_detail !== payload.explanation) {
      const technical = document.createElement('p');
      technical.className = 'source-health-technical';
      technical.textContent = payload.technical_detail;
      explanation.append(technical);
    }
    body.append(explanation);

    const facts = document.createElement('section');
    facts.className = 'source-health-facts';
    addDetail(facts, 'Last checked', root.last_checked_at);
    addDetail(facts, 'Last seen', root.last_seen_at);
    addDetail(facts, 'Last complete scan', root.last_scanned_at);
    addDetail(facts, 'Last known files', root.last_known_files);
    addDetail(facts, 'Observed files', root.observed_files);
    addDetail(facts, 'Protected catalog files', root.protected_files);
    if (facts.children.length) body.append(facts);

    const affected = Array.isArray(payload.affected) ? payload.affected : [];
    if (affected.length || Number(payload.affected_total || 0)) {
      const section = document.createElement('section');
      section.className = 'source-health-section';
      const heading = document.createElement('h3');
      const total = Number(payload.affected_total || affected.length);
      heading.textContent = `Affected media${total ? ` · ${total}` : ''}`;
      section.append(heading);
      const list = document.createElement('div');
      list.className = 'source-health-affected-list';
      affected.forEach((item) => {
        const article = document.createElement('article');
        const copyWrap = document.createElement('div');
        const itemTitle = document.createElement('strong');
        itemTitle.textContent = item.title_name || item.filename || 'Media file';
        const file = document.createElement('span');
        file.textContent = item.filename || '';
        const itemPath = document.createElement('code');
        itemPath.textContent = item.path || '';
        const issue = document.createElement('small');
        issue.textContent = item.summary || 'Source health issue';
        copyWrap.append(itemTitle, file, itemPath, issue);
        article.append(copyWrap);
        if (item.href) {
          const open = document.createElement('a');
          open.className = 'button small';
          open.href = item.href;
          open.textContent = 'Open title';
          article.append(open);
        }
        list.append(article);
      });
      if (payload.affected_truncated) {
        const note = document.createElement('p');
        note.className = 'muted';
        note.textContent = 'Only the first 100 affected media records are shown here. View the full source finding in Review.';
        list.append(note);
      } else if (!affected.length && total) {
        const note = document.createElement('p');
        note.className = 'muted';
        note.textContent = `${total.toLocaleString()} catalog file${total === 1 ? '' : 's'} are protected, but the last scan did not preserve enough detail to identify them individually. Related logs may contain more information.`;
        list.append(note);
      }
      section.append(list);
      body.append(section);
    }

    if (payload.recommendation) {
      const next = document.createElement('section');
      next.className = 'source-health-section';
      const heading = document.createElement('h3');
      heading.textContent = 'Recommended next step';
      const recommendation = document.createElement('p');
      recommendation.textContent = payload.recommendation;
      next.append(heading, recommendation);
      body.append(next);
    }
  };

  const openHealth = async (badge) => {
    const link = badge.closest('.root-library-link[data-source-id]');
    const row = badge.closest('.root-row');
    const sourceId = link?.dataset.sourceId;
    if (!sourceId) return;
    activeRow = row;
    title.textContent = link.querySelector('strong')?.textContent?.trim() || 'Source health';
    path.textContent = '';
    body.innerHTML = '<p class="muted">Loading source health details…</p>';
    reviewLink.href = '/review?bucket=sources';
    logsLink.href = '/logs';
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch(`/api/sources/${sourceId}/health-details`, {
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        let detail = '';
        try { detail = String((await response.json())?.detail || ''); } catch (_) {}
        throw new Error(detail || `InfoMancer returned HTTP ${response.status}.`);
      }
      render(await response.json());
    } catch (error) {
      body.replaceChildren();
      const notice = document.createElement('div');
      notice.className = 'notice error';
      notice.textContent = error?.message || 'InfoMancer could not load source health details.';
      body.append(notice);
    }
  };

  rescan?.addEventListener('click', () => {
    const button = activeRow?.querySelector('.source-action-rail form[action$="/scan"] button');
    close();
    button?.click();
  });

  const enhanceBadges = () => {
    document.querySelectorAll('.source-health-degraded, .source-health-offline').forEach((badge) => {
      badge.classList.add('source-health-action');
      badge.setAttribute('role', 'button');
      badge.setAttribute('tabindex', '0');
      badge.setAttribute('aria-label', `${badge.textContent.trim()}. Open source health details`);
      badge.setAttribute('title', 'Open source health details');
    });
  };
  enhanceBadges();

  document.addEventListener('click', (event) => {
    const badge = event.target.closest?.('.source-health-action');
    if (!badge) return;
    event.preventDefault();
    event.stopPropagation();
    openHealth(badge);
  }, true);
  document.addEventListener('keydown', (event) => {
    const badge = event.target.closest?.('.source-health-action');
    if (!badge || !['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    openHealth(badge);
  }, true);

  // Connection checks replace the server-rendered health node in place. Re-enhance
  // any new degraded/offline badge without adding another polling loop.
  const observer = new MutationObserver(enhanceBadges);
  sourceRows.forEach((row) => observer.observe(row, { childList: true, subtree: true }));
})();
