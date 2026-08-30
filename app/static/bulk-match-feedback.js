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
  const reviewForm = document.querySelector('[data-bulk-match-review-form]');
  const progressiveRows = new Map(
    [...document.querySelectorAll('[data-bulk-movie-id]')]
      .map((row) => [String(row.dataset.bulkMovieId || ''), row])
      .filter(([id]) => id),
  );
  let finishingAnalysis = false;
  let pendingAnalysisReload = false;
  let progressiveRequest = null;
  let progressiveAbortController = null;
  let lastProgressiveProcessed = 0;
  let queuedProgressiveProcessed = -1;
  let restoreRememberedCheckbox = () => {};

  const applyRunning = () => reviewForm?.dataset.bulkApplying === '1';
  const progressPhase = () => String(progress?.dataset.bulkMatchProgressPhase || '');
  const applyOwnsProgress = () => ['apply', 'complete', 'error'].includes(progressPhase());

  // Bulk review can contain dozens of remote provider posters. Keep their decoding
  // and network priority out of the interaction-critical path so Apply does not
  // finish into a burst of eager image decode/paint work on the renderer thread.
  const deferPoster = (poster) => {
    if (!(poster instanceof HTMLImageElement)) return;
    poster.loading = 'lazy';
    poster.decoding = 'async';
    poster.fetchPriority = 'low';
  };
  document.querySelectorAll('[data-bulk-match-review-form] img.poster-thumb').forEach(deferPoster);

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
    url.searchParams.set('return_to', window.location.pathname + window.location.search);
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
    if (!row || !row.isConnected) return;
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
          poster.loading = 'lazy';
          poster.decoding = 'async';
          poster.fetchPriority = 'low';
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
    if (!progressUrl || !progressiveRows.size) return Promise.resolve();
    const requested = Number.isFinite(processed) ? Math.max(0, processed) : 0;
    if (applyRunning()) {
      queuedProgressiveProcessed = Math.max(queuedProgressiveProcessed, requested);
      return Promise.resolve();
    }
    if (!force && requested <= lastProgressiveProcessed) return Promise.resolve();
    if (progressiveRequest) {
      queuedProgressiveProcessed = Math.max(queuedProgressiveProcessed, requested);
      return progressiveRequest;
    }

    const url = new URL(progressUrl, window.location.origin);
    url.searchParams.set('after', String(lastProgressiveProcessed));
    const abortController = new AbortController();
    progressiveAbortController = abortController;
    const timeoutId = window.setTimeout(() => abortController.abort(), 15000);
    progressiveRequest = fetch(url.pathname + url.search, {
      method: 'GET',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: abortController.signal,
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
        // Task progress remains authoritative. A transient or stalled row refresh
        // retries from the last successfully rendered index on the next task event.
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
        if (progressiveAbortController === abortController) progressiveAbortController = null;
        progressiveRequest = null;
        if (queuedProgressiveProcessed > lastProgressiveProcessed && !applyRunning()) {
          const queued = queuedProgressiveProcessed;
          queuedProgressiveProcessed = -1;
          refreshProgressiveMatches(queued);
        }
      });
    return progressiveRequest;
  };

  const renderAnalysisTask = (task) => {
    // Apply reuses this same card. Once Apply owns it, late task-center events from
    // the analysis phase may hydrate rows but must never rewrite Apply progress.
    if (applyRunning() || applyOwnsProgress()) return 0;
    const rawDetail = String(task?.detail || '');
    const detail = rawDetail
      .replace(/\s*Matches will appear in their rows as they are found\.?\s*$/i, '')
      .trim();
    const match = detail.match(/([\d,]+)\s+of\s+([\d,]+)\s+checked/i);
    let current = 0;
    if (match) {
      current = Number(match[1].replaceAll(',', ''));
      const total = Number(match[2].replaceAll(',', ''));
      if (progressCopy) {
        progressCopy.textContent = current > 0 ? detail : 'Preparing TVDB searches…';
      }
      if (progressFill && Number.isFinite(current) && Number.isFinite(total) && total > 0) {
        const percent = Math.max(0, Math.min(100, current / total * 100));
        progressFill.style.width = `${percent}%`;
      }
    } else if (progressCopy && detail) {
      progressCopy.textContent = detail;
    }
    if (progress) progress.hidden = false;
    return current;
  };

  const settleAnalysisCompletion = () => {
    if (applyRunning()) {
      pendingAnalysisReload = true;
      return;
    }

    // A selected movie review already contains placeholder rows for the complete
    // requested set, so its final persisted suggestions can hydrate in place. Other
    // review modes still need one normal reload to reveal rows that did not exist
    // when the page was rendered.
    const canFinishInPlace = Boolean(
      progressUrl && progressiveRows.size && matchOrigin === 'bulk-movie-selected',
    );
    if (canFinishInPlace) {
      const requested = Math.max(lastProgressiveProcessed + 1, queuedProgressiveProcessed);
      queuedProgressiveProcessed = -1;
      refreshProgressiveMatches(requested, true).finally(() => {
        if (applyOwnsProgress()) return;
        if (progressCopy) progressCopy.textContent = 'Analysis complete.';
        if (progressFill) progressFill.style.width = '100%';
      });
      return;
    }

    // If Apply has already completed or entered an error state, preserve that final
    // phase in the shared card until this review actually navigates away.
    if (!applyOwnsProgress()) {
      if (progressCopy) progressCopy.textContent = 'Matches ready. Loading the review…';
      if (progressFill) progressFill.style.width = '100%';
    }
    window.setTimeout(() => window.location.assign(completeUrl), 750);
  };

  document.addEventListener('infomancer:bulk-apply-started', () => {
    // Applying metadata already owns a provider-heavy operation. Abort any optional
    // row hydration request and let task progress queue the newest cursor instead of
    // competing for DOM/main-thread time while Apply is running.
    progressiveAbortController?.abort();
  });

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

      finishingAnalysis = true;
      settleAnalysisCompletion();
    });
  }

  if (!reviewForm) return;
  const status = reviewForm.querySelector('[data-bulk-apply-status]');
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

  const writeSelectionMemory = () => {
    try {
      if (Object.keys(rememberedSelection || {}).length) {
        window.sessionStorage.setItem(selectionMemoryKey, JSON.stringify(rememberedSelection));
      } else {
        window.sessionStorage.removeItem(selectionMemoryKey);
      }
    } catch (_) {}
  };

  const checkboxTitleId = (checkbox) => String(checkbox?.value || '').split(':', 1)[0];

  const rememberReviewSelection = () => {
    const next = { ...readSelectionMemory() };
    reviewForm.querySelectorAll('input[name="matches"]').forEach((checkbox) => {
      const titleId = checkboxTitleId(checkbox);
      if (titleId) next[titleId] = Boolean(checkbox.checked);
    });
    rememberedSelection = next;
    writeSelectionMemory();
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

  document.addEventListener('infomancer:bulk-match-applied', (event) => {
    const appliedIds = Array.isArray(event.detail?.appliedTitleIds)
      ? event.detail.appliedTitleIds.map((value) => String(value))
      : [];
    if (!appliedIds.length) return;
    const memory = { ...readSelectionMemory() };
    appliedIds.forEach((titleId) => {
      delete memory[titleId];
      progressiveRows.delete(titleId);
    });
    rememberedSelection = memory;
    writeSelectionMemory();
  });

  document.addEventListener('infomancer:bulk-apply-finished', () => {
    if (pendingAnalysisReload) {
      pendingAnalysisReload = false;
      settleAnalysisCompletion();
      return;
    }
    if (queuedProgressiveProcessed > lastProgressiveProcessed) {
      const queued = queuedProgressiveProcessed;
      queuedProgressiveProcessed = -1;
      refreshProgressiveMatches(queued);
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

  const showStatus = (message) => {
    if (!status) return;
    status.hidden = false;
    status.replaceChildren();
    const copy = document.createElement('span');
    copy.textContent = message;
    status.append(copy);
  };

  const completionMessage = new URLSearchParams(window.location.search).get('message') || '';
  if (/^Matched\s+\d+/i.test(completionMessage)) {
    showStatus(completionMessage);
  }
})();