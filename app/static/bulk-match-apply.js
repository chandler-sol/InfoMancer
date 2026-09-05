(() => {
  if (window.__infomancerBulkMatchApplyLoaded) return;
  window.__infomancerBulkMatchApplyLoaded = true;

  const reviewForm = document.querySelector('[data-bulk-match-review-form]');
  if (!reviewForm || typeof window.fetch !== 'function') return;

  const status = reviewForm.querySelector('[data-bulk-apply-status]');
  const applyButtons = [...reviewForm.querySelectorAll('[data-bulk-apply-button]')];
  const itemLabel = reviewForm.dataset.bulkMatchItemLabel || 'match';
  const itemPlural = reviewForm.dataset.bulkMatchItemPlural
    || (itemLabel.endsWith('series') ? itemLabel : `${itemLabel}s`);
  const kind = reviewForm.action.includes('/shows/') ? 'tv' : 'movie';
  const applyProgressUrl = kind === 'tv'
    ? '/api/shows/bulk-match/apply-progress'
    : '/api/movies/bulk-match/apply-progress';
  const applyTimeoutMs = 30 * 60 * 1000;
  let applyProgressTimer = 0;
  let applyProgressController = null;

  const showStatus = (message) => {
    if (!status) return;
    status.hidden = false;
    status.replaceChildren();
    const copy = document.createElement('span');
    copy.textContent = message;
    status.append(copy);
  };

  const hideStatus = () => {
    if (!status) return;
    status.hidden = true;
    status.replaceChildren();
  };

  const ensureWorkflowProgress = () => {
    let progress = document.querySelector('[data-bulk-match-progress]');
    if (progress) return progress;
    progress = document.createElement('section');
    progress.className = 'panel bulk-direct-progress bulk-workflow-progress';
    progress.dataset.bulkMatchProgress = '1';
    progress.innerHTML = `
      <div>
        <h2 data-bulk-match-progress-heading>Bulk Match</h2>
        <p class="muted" data-bulk-match-progress-copy></p>
      </div>
      <span class="task-track" aria-hidden="true"><i data-bulk-match-progress-fill></i></span>
    `;
    reviewForm.insertBefore(progress, status || reviewForm.firstChild);
    return progress;
  };

  const renderWorkflowProgress = ({ heading, copy, percent, phase = 'apply' }) => {
    const progress = ensureWorkflowProgress();
    progress.hidden = false;
    progress.dataset.bulkMatchProgressPhase = phase;
    progress.classList.toggle('is-complete', phase === 'complete');
    progress.classList.toggle('has-error', phase === 'error');
    const headingNode = progress.querySelector('[data-bulk-match-progress-heading]');
    const copyNode = progress.querySelector('[data-bulk-match-progress-copy]');
    const fill = progress.querySelector('[data-bulk-match-progress-fill]');
    if (headingNode && heading) headingNode.textContent = heading;
    if (copyNode) copyNode.textContent = copy || '';
    if (fill && Number.isFinite(Number(percent))) {
      fill.style.width = `${Math.max(0, Math.min(100, Number(percent)))}%`;
    }
    hideStatus();
  };

  const selectedMatches = () => [
    ...reviewForm.querySelectorAll('input[name="matches"]:checked'),
  ];

  const titleIdForCheckbox = (checkbox) => String(checkbox?.value || '').split(':', 1)[0];

  const csrfToken = () => (
    reviewForm.querySelector('input[name="csrf_token"]')?.value
    || document.body?.dataset?.csrfToken
    || ''
  ).trim();

  const rememberIdleLabels = () => {
    applyButtons.forEach((button) => {
      if (!button.dataset.bulkApplyIdleText) {
        button.dataset.bulkApplyIdleText = button.textContent.trim() || 'Apply selected matches';
      }
    });
  };

  const resetApplyState = () => {
    reviewForm.dataset.bulkApplying = '0';
    reviewForm.removeAttribute('aria-busy');
    applyButtons.forEach((button) => {
      button.disabled = false;
      button.textContent = button.dataset.bulkApplyIdleText || 'Apply selected matches';
    });
  };

  const selectionMemoryKey = () => {
    const scope = reviewForm.querySelector('input[name="selected_scope"]') ? 'selected' : 'review';
    return `infomancer:bulk-match-selection:${window.location.pathname}:${scope}`;
  };

  const purgeRememberedSelection = (titleIds) => {
    if (!titleIds.length) return;
    try {
      const key = selectionMemoryKey();
      const parsed = JSON.parse(window.sessionStorage.getItem(key) || '{}');
      const memory = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
      titleIds.forEach((titleId) => { delete memory[String(titleId)]; });
      if (Object.keys(memory).length) window.sessionStorage.setItem(key, JSON.stringify(memory));
      else window.sessionStorage.removeItem(key);
    } catch (_) {}

    const pendingKind = reviewForm.action.includes('/shows/') ? 'tv' : 'movie';
    try { window.sessionStorage.removeItem(`infomancer:bulk-match-return-pending:${pendingKind}`); } catch (_) {}
  };

  const formatJsonDetail = (detail) => {
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (!item || typeof item !== 'object') return String(item || '').trim();
          const location = Array.isArray(item.loc)
            ? item.loc.map(value => String(value)).filter(Boolean).join(' → ')
            : '';
          const message = String(item.msg || item.message || 'Invalid request').trim();
          return location ? `${location}: ${message}` : message;
        })
        .filter(Boolean)
        .join('; ');
    }
    if (detail && typeof detail === 'object') {
      const message = String(detail.msg || detail.message || '').trim();
      if (message) return message;
      try { return JSON.stringify(detail); } catch (_) { return 'Invalid request'; }
    }
    return String(detail || '').trim();
  };

  const responseDetail = async (response) => {
    let raw = '';
    try { raw = (await response.text()).trim(); } catch (_) {}
    if (!raw) return `HTTP ${response.status}`;
    if (raw.startsWith('<')) {
      try {
        const doc = new DOMParser().parseFromString(raw, 'text/html');
        const detail = doc.querySelector(
          '.notice.error, .auth-error p, main .error, main p, body p',
        )?.textContent?.replace(/\s+/g, ' ')?.trim();
        if (detail) return `HTTP ${response.status}: ${detail.slice(0, 320)}`;
      } catch (_) {}
      return `HTTP ${response.status}`;
    }
    try {
      const payload = JSON.parse(raw);
      const detail = formatJsonDetail(payload?.detail ?? payload?.message ?? '');
      if (detail) return `HTTP ${response.status}: ${detail.slice(0, 320)}`;
    } catch (_) {}
    return `HTTP ${response.status}: ${raw.replace(/\s+/g, ' ').slice(0, 320)}`;
  };

  const reviewRows = () => [...reviewForm.querySelectorAll('.table-wrap tbody tr')]
    .filter((row) => !row.querySelector('.empty'));

  const updateContinueLinks = () => reviewRows().length;

  const showEmptyPageState = () => {
    const tbody = reviewForm.querySelector('.table-wrap tbody');
    if (!tbody || reviewRows().length) return;
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.className = 'empty good';
    cell.textContent = 'All selected matches were applied.';
    row.append(cell);
    tbody.replaceChildren(row);
  };

  const applyResultInPlace = (payload, selected) => {
    const appliedIds = new Set(
      (Array.isArray(payload?.applied_title_ids) ? payload.applied_title_ids : [])
        .map(value => String(value)),
    );
    const appliedTitleIds = [...appliedIds];
    selected.forEach((checkbox) => {
      if (!appliedIds.has(titleIdForCheckbox(checkbox))) return;
      checkbox.closest('tr')?.remove();
    });
    purgeRememberedSelection(appliedTitleIds);
    document.dispatchEvent(new CustomEvent('infomancer:bulk-match-applied', {
      detail: { appliedTitleIds, payload },
    }));
    const remaining = updateContinueLinks();
    showEmptyPageState();
    return { appliedTitleIds, remaining };
  };

  const createApplyJobId = () => {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const random = Math.random().toString(36).slice(2);
    return `${Date.now().toString(36)}-${random}`;
  };

  const stopApplyProgressPolling = () => {
    if (applyProgressTimer) window.clearTimeout(applyProgressTimer);
    applyProgressTimer = 0;
    applyProgressController?.abort();
    applyProgressController = null;
  };

  const renderApplySnapshot = (snapshot, fallbackTotal, noun) => {
    const total = Math.max(0, Number(snapshot?.total || fallbackTotal || 0));
    const processed = Math.max(0, Number(snapshot?.processed || 0));
    const applied = Math.max(0, Number(snapshot?.applied || 0));
    const failed = Math.max(0, Number(snapshot?.failed || 0));
    const percent = total > 0 ? processed / total * 100 : 0;
    const copy = failed
      ? `${processed} of ${total} processed · ${applied} applied · ${failed} need attention.`
      : `${applied} of ${total} applied. You can keep using InfoMancer while this finishes.`;
    renderWorkflowProgress({
      heading: `Applying metadata for ${total || fallbackTotal} ${noun}`,
      copy,
      percent,
      phase: 'apply',
    });
  };

  const startApplyProgressPolling = (jobId, total, noun) => {
    stopApplyProgressPolling();
    renderApplySnapshot({ processed: 0, applied: 0, failed: 0, total }, total, noun);

    const poll = async () => {
      if (reviewForm.dataset.bulkApplying !== '1') return;
      const url = new URL(applyProgressUrl, window.location.origin);
      url.searchParams.set('job_id', jobId);
      const controller = new AbortController();
      applyProgressController = controller;
      const timeoutId = window.setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch(url.pathname + url.search, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (response.ok) renderApplySnapshot(await response.json(), total, noun);
      } catch (_) {
        // The POST remains authoritative. A transient progress-read failure should
        // never cancel an Apply that is still successfully running on the server.
      } finally {
        window.clearTimeout(timeoutId);
        if (applyProgressController === controller) applyProgressController = null;
        if (reviewForm.dataset.bulkApplying === '1') {
          applyProgressTimer = window.setTimeout(poll, 350);
        }
      }
    };

    applyProgressTimer = window.setTimeout(poll, 150);
  };

  const runApply = async (event) => {
    if (event.target !== reviewForm) return;
    event.preventDefault();
    if (reviewForm.dataset.bulkApplying === '1') return;

    const selected = selectedMatches();
    if (!selected.length) {
      showStatus('Select at least one suggested match before applying.');
      return;
    }

    const token = csrfToken();
    if (!token) {
      showStatus('InfoMancer could not verify this request. Reload the review and try again.');
      return;
    }

    rememberIdleLabels();
    reviewForm.dataset.bulkApplying = '1';
    reviewForm.setAttribute('aria-busy', 'true');
    const count = selected.length;
    const noun = count === 1 ? itemLabel : itemPlural;
    const jobId = createApplyJobId();
    applyButtons.forEach((button) => {
      button.disabled = true;
      button.textContent = count === 1 ? 'Applying match…' : `Applying ${count} matches…`;
    });
    startApplyProgressPolling(jobId, count, noun);
    document.dispatchEvent(new CustomEvent('infomancer:bulk-apply-started', {
      detail: { count, titleIds: selected.map(titleIdForCheckbox), jobId },
    }));

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), applyTimeoutMs);
    let finishDetail = { ok: false, appliedTitleIds: [] };
    try {
      const formData = new FormData(reviewForm);
      formData.set('apply_job_id', jobId);
      const response = await fetch(reviewForm.action, {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
        redirect: 'follow',
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          'X-CSRF-Token': token,
          'X-Requested-With': 'InfoMancerAsync',
        },
      });
      if (!response.ok) throw new Error(await responseDetail(response));

      const contentType = String(response.headers.get('content-type') || '').toLowerCase();
      if (!contentType.includes('application/json')) {
        window.location.assign(response.url || window.location.href);
        return;
      }

      const payload = await response.json();
      stopApplyProgressPolling();
      const { appliedTitleIds, remaining } = applyResultInPlace(payload, selected);
      resetApplyState();
      const failed = Number(payload?.failed || 0);
      const applied = Number(payload?.applied ?? appliedTitleIds.length);
      const message = String(payload?.message || `Matched ${applied} ${noun}`).trim();
      if (failed) {
        renderWorkflowProgress({
          heading: `${applied} match${applied === 1 ? '' : 'es'} applied · ${failed} need attention`,
          copy: `${message}. ${remaining} review row${remaining === 1 ? '' : 's'} remain.`,
          percent: 100,
          phase: 'error',
        });
      } else {
        const matchLabel = kind === 'movie' ? 'movie match' : 'TV series match';
        renderWorkflowProgress({
          heading: `${applied} ${matchLabel}${applied === 1 ? '' : 'es'} applied`,
          copy: remaining
            ? `Metadata has been saved successfully. ${remaining} review row${remaining === 1 ? '' : 's'} remain.`
            : 'Metadata has been saved successfully.',
          percent: 100,
          phase: 'complete',
        });
      }
      finishDetail = { ok: true, appliedTitleIds, failed, payload };
    } catch (error) {
      stopApplyProgressPolling();
      resetApplyState();
      if (error?.name === 'AbortError') {
        renderWorkflowProgress({
          heading: 'Apply status needs verification',
          copy: 'InfoMancer stopped waiting after 30 minutes. Some matches may already have completed. Reload this review before retrying and check Activity or Logs for the final state.',
          percent: 0,
          phase: 'error',
        });
        finishDetail = { ok: false, appliedTitleIds: [], aborted: true };
        return;
      }
      const detail = String(error?.message || error || 'Unknown request error').trim();
      renderWorkflowProgress({
        heading: 'Bulk Match apply could not finish',
        copy: `${detail}. The rest of InfoMancer remains available; retry when ready or open Activity or Logs for details.`,
        percent: 0,
        phase: 'error',
      });
      finishDetail = { ok: false, appliedTitleIds: [], error: detail };
    } finally {
      window.clearTimeout(timeoutId);
      stopApplyProgressPolling();
      document.dispatchEvent(new CustomEvent('infomancer:bulk-apply-finished', {
        detail: finishDetail,
      }));
    }
  };

  document.addEventListener('submit', runApply, true);

  document.querySelectorAll('.review-actions .actions').forEach((actions) => {
    const apply = actions.querySelector('[data-bulk-apply-button]');
    if (!apply || actions.querySelector('[data-bulk-clear-selection]')) return;
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'button';
    clear.dataset.bulkClearSelection = '1';
    clear.textContent = 'Clear selection';
    clear.addEventListener('click', () => {
      reviewForm.querySelectorAll('input[name="matches"]:checked').forEach((checkbox) => {
        checkbox.checked = false;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      });
      showStatus('Selection cleared.');
    });
    actions.insertBefore(clear, apply);
  });
})();
