(() => {
  const reviewForm = document.querySelector('[data-bulk-match-review-form]');
  if (!reviewForm || typeof window.fetch !== 'function') return;

  const status = reviewForm.querySelector('[data-bulk-apply-status]');
  const applyButtons = [...reviewForm.querySelectorAll('[data-bulk-apply-button]')];
  const itemLabel = reviewForm.dataset.bulkMatchItemLabel || 'match';
  const itemPlural = reviewForm.dataset.bulkMatchItemPlural
    || (itemLabel.endsWith('series') ? itemLabel : `${itemLabel}s`);
  const applyTimeoutMs = 30 * 60 * 1000;

  const showStatus = (message, working = false) => {
    if (!status) return;
    status.hidden = false;
    status.replaceChildren();
    const copy = document.createElement('span');
    copy.textContent = message;
    status.append(copy);
    if (working) {
      const track = document.createElement('span');
      track.className = 'task-track';
      track.setAttribute('aria-hidden', 'true');
      track.append(document.createElement('i'));
      status.append(track);
    }
  };

  const selectedMatches = () => [
    ...reviewForm.querySelectorAll('input[name="matches"]:checked'),
  ];

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

  const clearRememberedSelection = () => {
    const scope = reviewForm.querySelector('input[name="selected_scope"]') ? 'selected' : 'review';
    const key = `infomancer:bulk-match-selection:${window.location.pathname}:${scope}`;
    try { window.sessionStorage.removeItem(key); } catch (_) {}
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
      true,
    );

    /* The capture-phase handler owns Bulk Apply. The older feedback script still
       supports progressive review and selection memory, but its compatibility
       submit listener sees data-bulk-applying=1 and exits. Keeping this request a
       normal fetch avoids WebView2's keepalive lifecycle path, which can wedge an
       embedded page after an immediate rejection. */
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), applyTimeoutMs);
    try {
      const response = await fetch(reviewForm.action, {
        method: 'POST',
        body: new FormData(reviewForm),
        credentials: 'same-origin',
        redirect: 'follow',
        signal: controller.signal,
        headers: {
          Accept: 'text/html',
          'X-CSRF-Token': token,
          'X-Requested-With': 'InfoMancerAsync',
        },
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      clearRememberedSelection();
      window.location.assign(response.url || window.location.href);
    } catch (error) {
      resetApplyState();
      if (error?.name === 'AbortError') {
        showStatus(
          'InfoMancer stopped waiting for this Apply request after 30 minutes. '
          + 'Some matches may already have completed. Reload this review before retrying and check Activity/Logs for the final state.',
          false,
        );
        return;
      }
      const detail = String(error?.message || error || 'Unknown request error').trim();
      showStatus(
        `InfoMancer could not finish applying these matches. ${detail}. `
        + 'The rest of the app remains available; retry when ready or open Activity/Logs for details.',
        false,
      );
    } finally {
      window.clearTimeout(timeoutId);
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
