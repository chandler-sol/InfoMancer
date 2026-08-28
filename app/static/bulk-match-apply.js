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
  const applyTimeoutMs = 30 * 60 * 1000;

  const showStatus = (message) => {
    if (!status) return;
    status.hidden = false;
    status.replaceChildren();
    const copy = document.createElement('span');
    copy.textContent = message;
    status.append(copy);
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

    // The selected-search handoff used by Workspace previously expected a full
    // redirect after Apply. In-place Apply deliberately keeps the user on this
    // review, so clear that one-shot redirect marker when the batch succeeds.
    const kind = reviewForm.action.includes('/shows/') ? 'tv' : 'movie';
    try { window.sessionStorage.removeItem(`infomancer:bulk-match-return-pending:${kind}`); } catch (_) {}
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

  const updateContinueLinks = () => {
    const remaining = reviewRows().length;
    const currentUrl = new URL(window.location.href);
    const currentOffset = Math.max(0, Number.parseInt(currentUrl.searchParams.get('offset') || '0', 10) || 0);
    reviewForm.querySelectorAll('.review-actions a.button').forEach((link) => {
      if (!/^Next 50$/i.test(link.textContent.trim()) && !/^Continue review$/i.test(link.textContent.trim())) return;
      const url = new URL(link.href, window.location.origin);
      url.searchParams.set('offset', String(currentOffset + remaining));
      link.href = url.pathname + url.search;
      link.textContent = 'Continue review';
    });
    return remaining;
  };

  const showEmptyPageState = () => {
    const tbody = reviewForm.querySelector('.table-wrap tbody');
    if (!tbody || reviewRows().length) return;
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.className = 'empty good';
    cell.textContent = 'All selected matches on this page were applied. Continue reviewing when ready.';
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
      detail: {appliedTitleIds, payload},
    }));
    const remaining = updateContinueLinks();
    showEmptyPageState();
    return {appliedTitleIds, remaining};
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
    applyButtons.forEach((button) => {
      button.disabled = true;
      button.textContent = count === 1 ? 'Applying match…' : `Applying ${count} matches…`;
    });
    showStatus(
      `Applying ${count} selected ${noun}. InfoMancer is fetching and saving metadata. `
      + 'You can keep using InfoMancer while this finishes.',
    );
    document.dispatchEvent(new CustomEvent('infomancer:bulk-apply-started', {
      detail: {count, titleIds: selected.map(titleIdForCheckbox)},
    }));

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), applyTimeoutMs);
    let finishDetail = {ok: false, appliedTitleIds: []};
    try {
      const response = await fetch(reviewForm.action, {
        method: 'POST',
        body: new FormData(reviewForm),
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
        // Compatibility fallback for an older core. Do not parse/rewrite returned
        // HTML in the current document; a normal navigation is safer in that case.
        window.location.assign(response.url || window.location.href);
        return;
      }

      const payload = await response.json();
      const {appliedTitleIds, remaining} = applyResultInPlace(payload, selected);
      resetApplyState();
      const failed = Number(payload?.failed || 0);
      const message = String(payload?.message || `Matched ${appliedTitleIds.length} ${noun}`).trim();
      showStatus(
        `${message}. ${remaining} review row${remaining === 1 ? '' : 's'} remain on this page.`,
      );
      finishDetail = {ok: true, appliedTitleIds, failed, payload};
    } catch (error) {
      resetApplyState();
      if (error?.name === 'AbortError') {
        showStatus(
          'InfoMancer stopped waiting for this Apply request after 30 minutes. '
          + 'Some matches may already have completed. Reload this review before retrying and check Activity/Logs for the final state.',
        );
        finishDetail = {ok: false, appliedTitleIds: [], aborted: true};
        return;
      }
      const detail = String(error?.message || error || 'Unknown request error').trim();
      showStatus(
        `InfoMancer could not finish applying these matches. ${detail}. `
        + 'The rest of the app remains available; retry when ready or open Activity/Logs for details.',
      );
      finishDetail = {ok: false, appliedTitleIds: [], error: detail};
    } finally {
      window.clearTimeout(timeoutId);
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
