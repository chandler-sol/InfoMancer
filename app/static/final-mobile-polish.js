(() => {
  const csrfToken = document.body?.dataset.csrfToken || '';
  const cancelableTasks = new Map([
    ['scan-all', 'Cancel scan'],
    ['media-fingerprints', 'Cancel fingerprinting'],
  ]);
  const stopping = new Set();
  let liveTasks = [];

  const installFingerprintScheduleHandoff = () => {
    const form = document.getElementById('hashing-settings-form');
    if (!form || form.querySelector('.fingerprint-schedule-handoff')) return;

    const grid = form.querySelector('.settings-form-grid');
    if (!grid) return;

    const handoff = document.createElement('div');
    handoff.className = 'fingerprint-schedule-handoff';
    const copy = document.createElement('span');
    copy.textContent = 'Fingerprint cadence is managed in Scheduled Tasks so there is only one schedule to maintain.';
    const link = document.createElement('a');
    link.className = 'button';
    link.href = '/settings/scheduled-tasks';
    link.textContent = 'Open Scheduled Tasks';
    handoff.append(copy, link);
    grid.after(handoff);
  };

  const cancelTask = async (task, button, row) => {
    if (!task?.id || stopping.has(task.id)) return;
    stopping.add(task.id);
    button.disabled = true;
    button.textContent = 'Stopping…';

    try {
      const response = await fetch(`/api/tasks/${encodeURIComponent(task.id)}/cancel`, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          'Accept': 'application/json',
          ...(csrfToken ? {'X-CSRF-Token': csrfToken} : {}),
        },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      const detail = row.querySelector('small');
      if (detail && data.detail) detail.textContent = data.detail;
    } catch (error) {
      stopping.delete(task.id);
      button.disabled = false;
      button.textContent = 'Try cancel again';
      button.title = error?.message || 'The cancellation request could not be sent.';
    }
  };

  const decorateTaskRows = () => {
    const list = document.getElementById('task-list');
    if (!list) return;
    const rows = [...list.querySelectorAll('.task-row.task-active')];

    rows.forEach((row, index) => {
      const task = liveTasks[index];
      if (!task || !cancelableTasks.has(task.id)) return;
      if (row.querySelector('.task-cancel-action')) return;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'task-inline-action task-cancel-action';
      button.textContent = stopping.has(task.id)
        ? 'Stopping…'
        : cancelableTasks.get(task.id);
      button.disabled = stopping.has(task.id);
      button.addEventListener('click', () => cancelTask(task, button, row));
      row.append(button);
    });
  };

  document.addEventListener('infomancer:tasks', (event) => {
    liveTasks = Array.isArray(event.detail?.tasks) ? event.detail.tasks : [];
    const activeIds = new Set(liveTasks.map((task) => task.id));
    for (const taskId of [...stopping]) {
      if (!activeIds.has(taskId)) stopping.delete(taskId);
    }
    queueMicrotask(decorateTaskRows);
  });

  const installDomEnhancements = () => {
    const taskList = document.getElementById('task-list');
    if (taskList && taskList.dataset.cancelObserver !== '1') {
      taskList.dataset.cancelObserver = '1';
      new MutationObserver(() => queueMicrotask(decorateTaskRows)).observe(taskList, {
        childList: true,
        subtree: true,
      });
    }
    installFingerprintScheduleHandoff();
    decorateTaskRows();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installDomEnhancements, {once: true});
  } else {
    installDomEnhancements();
  }
})();
