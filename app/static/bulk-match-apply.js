(() => {
  const reviewForm = document.querySelector('[data-bulk-match-review-form]');
  if (!reviewForm || typeof window.fetch !== 'function') return;

  const status = reviewForm.querySelector('[data-bulk-apply-status]');
  const applyButtons = [...reviewForm.querySelectorAll('[data-bulk-apply-button]')];
  const itemLabel = reviewForm.dataset.bulkMatchItemLabel || 'match';
  const itemPlural = reviewForm.dataset.bulkMatchItemPlural
    || (itemLabel.endsWith('series') ? itemLabel : `${itemLabel}s`);

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
      const detail = String(payload?.detail || payload?.message || '').trim();
      if (detail) return `HTTP ${response.status}: ${detail.slice(0, 320)}`;
    } catch (_) {}
    return `HTTP ${response.status}: ${raw.replace(/\s+/g, ' ').slice(0, 320)}`;
  };

  const runApply = async (event) => {
    if (event.target !== reviewForm) return;
    event.preventDefault();
    // This capture-phase handler intentionally supersedes the older synchronous-
    // navigation compatibility handler in bulk-match-feedback.js.
    event.stopImmediatePropagation();
    if (reviewForm.dataset.bulkApplying === '1') return;

    const selected = selectedMatches();
    if (!selected.length) {
      showStatus('Select at least one suggested match before applying.');
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

    try {
      const response = await fetch(reviewForm.action, {
        method: 'POST',
        body: new FormData(reviewForm),
        credentials: 'same-origin',
        redirect: 'follow',
        headers: {
          Accept: 'text/html',
          'X-Requested-With': 'InfoMancerAsync',
        },
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      clearRememberedSelection();
      window.location.assign(response.url || window.location.href);
    } catch (error) {
      resetApplyState();
      const detail = String(error?.message || error || 'Unknown request error').trim();
      showStatus(
        `InfoMancer could not finish applying these matches. ${detail}. `
        + 'The rest of the app remains available; retry when ready or open Activity/Logs for details.',
        false,
      );
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
