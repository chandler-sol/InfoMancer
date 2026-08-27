(() => {
  const controller = document.querySelector('[data-bulk-match-controller]');
  if (!controller) return;

  const taskId = controller.dataset.bulkMatchTask || '';
  const completeUrl = controller.dataset.bulkMatchCompleteUrl || window.location.href;
  const progressUrl = controller.dataset.bulkMatchProgressUrl || '';
  const matchOrigin = controller.dataset.bulkMatchOrigin || 'bulk-movie';
  const analysisActiveAtRender = controller.dataset.bulkMatchActive === '1';
  const progress = document.querySelector('[data-bulk-match-progress]');
  const progressCopy = document.querySelector('[data-bulk-match-progress-copy]');
  const progressFill = document.querySelector('[data-bulk-match-progress-fill]');
  const progressiveRows = new Map(
    [...document.querySelectorAll('[data-bulk-movie-id]')]
      .map((row) => [String(row.dataset.bulkMovieId || ''), row])
      .filter(([id]) => id),
  );
  let finishingAnalysis = false;
  let progressiveRequest = null;
  let lastProgressiveProcessed = -1;
  let queuedProgressiveProcessed = -1;
  let restoreRememberedCheckbox = () => {};

  /* Bulk review tables can be very tall. Keep active feedback below the persistent
     application header so an action started from the bottom of the table is still
     visible without moving the user's scroll position. */
  const makeFeedbackSticky = (node) => {
    if (!node) return;
    node.style.position = 'sticky';
    node.style.top = '80px';
    node.style.zIndex = '4';
    node.style.boxShadow = '0 14px 32px rgba(0, 0, 0, .32)';
  };
  makeFeedbackSticky(progress);

  const manualMatchLink = (item, candidate, possible) => {
    const link = document.createElement('a');
    link.className = 'possible-match-link';
    const url = new URL(`/titles/${item.title_id}/tvdb`, window.location.origin);
    const query = String(candidate?.search_query || item.library_title || '');
    if (query) url.searchParams.set('q', query);
    url.searchParams.set('from', matchOrigin);
    link.href = url.pathname + url.search;
    link.textContent = candidate
      ? (possible ? 'Review all possible matches' : 'Find another match')
      : 'Try manual search';
    return link;
  };

  const confidenceClass = (score) => {
    if (score >= 95) return 'very-high';
    if (score >= 80) return 'high';
    if (score >= 60) return 'medium';
    return 'low';
  };

  const renderProgressiveItem = (item) => {
    const row = progressiveRows.get(String(item?.title_id || ''));
    if (!row) return;
    const suggestionCell = row.querySelector('[data-bulk-suggestion-cell]');
    const confidenceCell = row.querySelector('[data-bulk-confidence-cell]');
    const applyCell = row.querySelector('[data-bulk-apply-cell]');
    const candidate = item?.candidate || null;
    const scoreValue = item?.confidence_score;
    const score = Number(scoreValue);
    const hasScore = scoreValue !== null && scoreValue !== undefined && Number.isFinite(score);
    const possible = Boolean(candidate?.possible_match) || (hasScore && score < 80);

    if (applyCell) {
      applyCell.replaceChildren();
      if (candidate?.id) {
        const checkbox = document.createElement('input');
        checkbox.className = 'match-check';
        checkbox.type = 'checkbox';
        checkbox.name = 'matches';
        checkbox.value = `${item.title_id}:${candidate.id}`;
        checkbox.checked = Boolean(item.exact);
        restoreRememberedCheckbox(checkbox);
        applyCell.append(checkbox);
      }
    }

    if (suggestionCell) {
      suggestionCell.replaceChildren();
      if (candidate) {
        const titleCell = document.createElement('div');
        titleCell.className = 'title-cell';
        if (candidate.image_url) {
          const poster = document.createElement('img');
          poster.className = 'poster-thumb';
          poster.src = candidate.image_url;
          poster.alt = '';
          titleCell.append(poster);
        }
        const details = document.createElement('div');
        if (possible) {
          const flag = document.createElement('span');
          flag.className = 'possible-match-label';
          flag.textContent = 'Possible match';
          details.append(flag);
        }
        const title = document.createElement('strong');
        title.textContent = `${candidate.name || 'TVDB result'}${candidate.year ? ` (${candidate.year})` : ''}`;
        details.append(title);
        const metadata = document.createElement('small');
        const resultCount = Number(item.result_count || 0);
        metadata.textContent = `TVDB ${candidate.id || '?'} · ${resultCount} search result(s)`;
        details.append(metadata, manualMatchLink(item, candidate, possible));
        titleCell.append(details);
        suggestionCell.append(titleCell);
      } else {
        const state = document.createElement('span');
        state.className = item?.error ? 'error-text' : 'muted';
        state.textContent = item?.error ? 'Lookup error' : 'No result';
        suggestionCell.append(state, manualMatchLink(item, null, false));
      }
    }

    if (confidenceCell) {
      confidenceCell.replaceChildren();
      if (hasScore) {
        const badge = document.createElement('span');
        badge.className = `confidence-badge ${confidenceClass(score)}`;
        badge.textContent = `${score}% · ${item.confidence_label || ''}`.trim();
        confidenceCell.append(badge);
      } else {
        confidenceCell.textContent = '-';
      }
    }
  };

  const refreshProgressiveMatches = (processed, force = false) => {
    if (!progressUrl || !progressiveRows.size) return;
    const requested = Number.isFinite(processed) ? processed : 0;
    if (!force && requested <= lastProgressiveProcessed) return;
    if (progressiveRequest) {
      queuedProgressiveProcessed = Math.max(queuedProgressiveProcessed, requested);
      return;
    }
    progressiveRequest = fetch(progressUrl, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
      .then((response) => {
        if (!response.ok) throw new Error(`Progressive match request failed: ${response.status}`);
        return response.json();
      })
      .then((payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        items.forEach(renderProgressiveItem);
        lastProgressiveProcessed = Math.max(
          lastProgressiveProcessed,
          Number(payload?.processed || requested || 0),
        );
      })
      .catch(() => {
        // Task progress remains authoritative. A transient row-refresh failure can
        // retry on the next task event without interrupting the background job.
      })
      .finally(() => {
        progressiveRequest = null;
        if (queuedProgressiveProcessed > lastProgressiveProcessed) {
          const queued = queuedProgressiveProcessed;
          queuedProgressiveProcessed = -1;
          refreshProgressiveMatches(queued);
        }
      });
  };

  const renderAnalysisTask = (task) => {
    const detail = String(task?.detail || '');
    const match = detail.match(/([\d,]+)\s+of\s+([\d,]+)\s+checked/i);
    let current = 0;
    if (match) {
      current = Number(match[1].replaceAll(',', ''));
      const total = Number(match[2].replaceAll(',', ''));
      if (progressCopy) {
        progressCopy.textContent = current > 0
          ? `${detail}. Matches will appear in their rows as they are found.`
          : 'Preparing TVDB searches… Matches will appear here as they are found.';
      }
      if (progressFill && Number.isFinite(current) && Number.isFinite(total) && total > 0) {
        const percent = Math.max(0, Math.min(100, current / total * 100));
        progressFill.style.width = `${percent}%`;
      }
    } else if (progressCopy && detail) {
      progressCopy.textContent = `${detail}. Matches will appear in their rows as they are found.`;
    }
    if (progress) progress.hidden = false;
    return current;
  };

  if (analysisActiveAtRender && taskId) {
    refreshProgressiveMatches(0, true);
    document.addEventListener('infomancer:tasks', (event) => {
      if (finishingAnalysis) return;
      const tasks = Array.isArray(event.detail?.tasks) ? event.detail.tasks : [];
      const task = tasks.find((candidate) => candidate.id === taskId);
      if (task) {
        const processed = renderAnalysisTask(task);
        refreshProgressiveMatches(processed);
        return;
      }

      // The server rendered this page while the analysis was active. Once the
      // canonical task poller reports that task absent, the saved suggestions are
      // ready. Use that existing signal instead of adding a second task poller.
      finishingAnalysis = true;
      refreshProgressiveMatches(Number.MAX_SAFE_INTEGER, true);
      if (progressCopy) progressCopy.textContent = 'Matches ready. Loading the review…';
      if (progressFill) progressFill.style.width = '100%';
      window.setTimeout(() => window.location.assign(completeUrl), 350);
    });
  }

  const reviewForm = document.querySelector('[data-bulk-match-review-form]');
  if (!reviewForm) return;
  const status = reviewForm.querySelector('[data-bulk-apply-status]');
  const applyButtons = [...reviewForm.querySelectorAll('[data-bulk-apply-button]')];
  const itemLabel = reviewForm.dataset.bulkMatchItemLabel || 'match';
  const itemPlural = reviewForm.dataset.bulkMatchItemPlural
    || (itemLabel.endsWith('series') ? itemLabel : `${itemLabel}s`);
  const selectionScope = reviewForm.querySelector('input[name="selected_scope"]')
    ? 'selected'
    : 'review';
  const selectionMemoryKey = `infomancer:bulk-match-selection:${window.location.pathname}:${selectionScope}`;
  let rememberedSelection = null;
  let clearSelectionOnPageHide = false;

  const readSelectionMemory = () => {
    if (rememberedSelection !== null) return rememberedSelection;
    try {
      const parsed = JSON.parse(window.sessionStorage.getItem(selectionMemoryKey) || '{}');
      rememberedSelection = parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? parsed
        : {};
    } catch (_) {
      rememberedSelection = {};
    }
    return rememberedSelection;
  };

  const checkboxTitleId = (checkbox) => String(checkbox?.value || '').split(':', 1)[0];

  const rememberReviewSelection = () => {
    const next = { ...readSelectionMemory() };
    reviewForm.querySelectorAll('input[name="matches"]').forEach((checkbox) => {
      const titleId = checkboxTitleId(checkbox);
      if (titleId) next[titleId] = Boolean(checkbox.checked);
    });
    rememberedSelection = next;
    try {
      window.sessionStorage.setItem(selectionMemoryKey, JSON.stringify(next));
    } catch (_) {
      // Selection memory is a convenience only. Review remains usable if storage
      // is unavailable or disabled by the WebView/browser.
    }
  };

  const clearReviewSelection = () => {
    rememberedSelection = {};
    try {
      window.sessionStorage.removeItem(selectionMemoryKey);
    } catch (_) {
      // Leaving the review must not be blocked by optional selection memory.
    }
  };

  restoreRememberedCheckbox = (checkbox) => {
    const titleId = checkboxTitleId(checkbox);
    const memory = readSelectionMemory();
    if (titleId && Object.prototype.hasOwnProperty.call(memory, titleId)) {
      checkbox.checked = Boolean(memory[titleId]);
    }
  };

  reviewForm.querySelectorAll('input[name="matches"]').forEach(restoreRememberedCheckbox);
  reviewForm.addEventListener('change', (event) => {
    if (event.target instanceof HTMLInputElement && event.target.name === 'matches') {
      rememberReviewSelection();
    }
  });

  document.addEventListener('click', (event) => {
    const link = event.target.closest?.('a');
    if (!link) return;
    if (link.classList.contains('possible-match-link')) {
      rememberReviewSelection();
      return;
    }
    if (!link.classList.contains('back') && !link.closest('.review-actions')) return;
    const destination = new URL(link.href, window.location.origin);
    const staysInReview = destination.pathname === window.location.pathname
      && destination.searchParams.get('review') === 'true';
    if (!staysInReview) {
      // Defer removal until pagehide. If navigation is cancelled or opened in a
      // separate tab, the current review keeps its remembered checkbox state.
      clearSelectionOnPageHide = true;
    }
  });
  window.addEventListener('pagehide', () => {
    if (clearSelectionOnPageHide) {
      clearReviewSelection();
    } else {
      rememberReviewSelection();
    }
  });

  makeFeedbackSticky(status);

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

  const completionMessage = new URLSearchParams(window.location.search).get('message') || '';
  if (/^Matched\s+\d+/i.test(completionMessage)) {
    showStatus(completionMessage, false);
  }

  reviewForm.addEventListener('submit', (event) => {
    if (reviewForm.dataset.bulkApplying === '1') {
      event.preventDefault();
      return;
    }
    const selected = [...reviewForm.querySelectorAll('input[name="matches"]:checked')];
    if (!selected.length) {
      event.preventDefault();
      showStatus('Select at least one suggested match before applying.');
      return;
    }

    event.preventDefault();
    rememberReviewSelection();
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
      + 'Unselected or unresolved items will remain here for review.',
      true,
    );

    // Give WebView a paint opportunity so the busy state and indeterminate progress
    // track are visible before the synchronous bulk POST begins its provider lookups.
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => HTMLFormElement.prototype.submit.call(reviewForm));
    });
  });
})();
