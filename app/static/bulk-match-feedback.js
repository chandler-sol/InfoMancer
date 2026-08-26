(() => {
  const controller = document.querySelector('[data-bulk-match-controller]');
  if (!controller) return;

  const taskId = controller.dataset.bulkMatchTask || '';
  const completeUrl = controller.dataset.bulkMatchCompleteUrl || window.location.href;
  const analysisActiveAtRender = controller.dataset.bulkMatchActive === '1';
  const progress = document.querySelector('[data-bulk-match-progress]');
  const progressCopy = document.querySelector('[data-bulk-match-progress-copy]');
  const progressFill = document.querySelector('[data-bulk-match-progress-fill]');
  let finishingAnalysis = false;

  const renderAnalysisTask = (task) => {
    const detail = String(task?.detail || '');
    const match = detail.match(/([\d,]+)\s+of\s+([\d,]+)\s+checked/i);
    if (match) {
      const current = Number(match[1].replaceAll(',', ''));
      const total = Number(match[2].replaceAll(',', ''));
      if (progressCopy) {
        progressCopy.textContent = current > 0
          ? `${detail}. This page will update automatically.`
          : 'Preparing TVDB searches… This page will update automatically.';
      }
      if (progressFill && Number.isFinite(current) && Number.isFinite(total) && total > 0) {
        const percent = Math.max(0, Math.min(100, current / total * 100));
        progressFill.style.width = `${percent}%`;
      }
    } else if (progressCopy && detail) {
      progressCopy.textContent = `${detail}. This page will update automatically.`;
    }
    if (progress) progress.hidden = false;
  };

  if (analysisActiveAtRender && taskId) {
    document.addEventListener('infomancer:tasks', (event) => {
      if (finishingAnalysis) return;
      const tasks = Array.isArray(event.detail?.tasks) ? event.detail.tasks : [];
      const task = tasks.find((candidate) => candidate.id === taskId);
      if (task) {
        renderAnalysisTask(task);
        return;
      }

      // The server rendered this page while the analysis was active. Once the
      // canonical task poller reports that task absent, the saved suggestions are
      // ready. Use that existing signal instead of adding another API polling loop.
      finishingAnalysis = true;
      if (progressCopy) progressCopy.textContent = 'Matches ready. Loading the review…';
      if (progressFill) progressFill.style.width = '100%';
      window.setTimeout(() => window.location.assign(completeUrl), 250);
    });
  }

  const reviewForm = document.querySelector('[data-bulk-match-review-form]');
  if (!reviewForm) return;
  const status = reviewForm.querySelector('[data-bulk-apply-status]');
  const applyButtons = [...reviewForm.querySelectorAll('[data-bulk-apply-button]')];
  const itemLabel = reviewForm.dataset.bulkMatchItemLabel || 'match';
  const itemPlural = reviewForm.dataset.bulkMatchItemPlural || `${itemLabel}s`;

  reviewForm.addEventListener('submit', (event) => {
    if (reviewForm.dataset.bulkApplying === '1') {
      event.preventDefault();
      return;
    }
    const selected = [...reviewForm.querySelectorAll('input[name="matches"]:checked')];
    if (!selected.length) {
      event.preventDefault();
      if (status) {
        status.hidden = false;
        status.textContent = 'Select at least one suggested match before applying.';
      }
      return;
    }

    event.preventDefault();
    reviewForm.dataset.bulkApplying = '1';
    reviewForm.setAttribute('aria-busy', 'true');
    const count = selected.length;
    const noun = count === 1 ? itemLabel : itemPlural;
    applyButtons.forEach((button) => {
      button.disabled = true;
      button.textContent = count === 1 ? 'Applying match…' : `Applying ${count} matches…`;
    });
    if (status) {
      status.hidden = false;
      status.textContent = `Applying ${count} selected ${noun}. InfoMancer is fetching and saving metadata. Keep this window open.`;
    }

    // Give WebView a paint opportunity so the busy state is visible before the
    // synchronous bulk POST begins its provider lookups.
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => HTMLFormElement.prototype.submit.call(reviewForm));
    });
  });
})();
