(() => {
  const widget = document.getElementById("task-widget");
  const toggle = document.getElementById("task-widget-toggle");
  const popover = document.getElementById("task-popover");
  const summary = document.getElementById("task-summary");
  const cardDetail = document.getElementById("task-card-detail");
  const list = document.getElementById("task-list");
  if (!widget || !toggle || !popover || !summary || !cardDetail || !list) return;

  const heading = popover.querySelector(".task-popover-heading strong");
  if (heading) heading.textContent = "Tasks";

  const COMPLETE_TTL_MS = 600000;
  const RECENT_KEY = "infomancer-task-complete-v1";
  const FAILURE_ACK_KEY = "infomancer-task-failure-acks-v1";

  const read = (key, fallback) => {
    try {
      return JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
    } catch (_error) {
      return fallback;
    }
  };

  const write = (key, value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_error) {}
  };

  const savedRecent = read(RECENT_KEY, []);
  const savedAcks = read(FAILURE_ACK_KEY, []);
  let recent = Array.isArray(savedRecent) ? savedRecent : [];
  let acknowledgements = new Set(Array.isArray(savedAcks) ? savedAcks : []);
  let active = [];
  let previous = new Map();
  let failures = [];
  let open = widget.classList.contains("is-pinned") && !popover.hidden;

  const failureSignature = (task) => `${task.id}|${task.detail || task.label || "failed"}`;

  const pruneRecent = () => {
    const next = recent.filter((task) => Number(task.expiresAt) > Date.now());
    if (next.length !== recent.length) {
      recent = next;
      write(RECENT_KEY, recent);
    }
  };

  const taskProgress = (task) => {
    const match = String(task.detail || "").match(/([\d,]+)\s+of\s+([\d,]+)/i);
    if (!match) return null;
    const current = Number(match[1].replaceAll(",", ""));
    const total = Number(match[2].replaceAll(",", ""));
    return Number.isFinite(current) && total > 0 ? {current, total} : null;
  };

  const actionButton = (text, handler) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "task-inline-action";
    button.textContent = text;
    button.addEventListener("click", handler);
    return button;
  };

  const activeRow = (task) => {
    const row = document.createElement("div");
    row.className = "task-row task-active";

    const label = document.createElement("strong");
    label.textContent = task.label;
    const detail = document.createElement("small");
    detail.textContent = task.detail || "Working in the background";
    row.append(label, detail);

    const progress = taskProgress(task);
    const track = document.createElement("span");
    const fill = document.createElement("i");
    track.className = `task-track ${progress ? "determinate" : "indeterminate"}`;
    track.setAttribute("role", "progressbar");
    track.append(fill);

    if (progress) {
      const percent = Math.max(0, Math.min(100, progress.current / progress.total * 100));
      fill.style.width = `${percent}%`;
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", String(progress.total));
      track.setAttribute("aria-valuenow", String(Math.min(progress.current, progress.total)));
      const copy = document.createElement("span");
      copy.className = "task-progress-copy";
      copy.textContent = `${percent.toFixed(percent >= 10 ? 0 : 1)}%`;
      row.append(track, copy);
    } else {
      track.setAttribute("aria-label", "Preparing task progress");
      row.append(track);
    }

    return row;
  };

  const completeRow = (task) => {
    const row = document.createElement("div");
    row.className = "task-row task-notification task-complete";

    const head = document.createElement("div");
    head.className = "task-notification-head";
    const title = document.createElement("span");
    const badge = document.createElement("b");
    const label = document.createElement("strong");
    badge.className = "task-state-badge complete";
    badge.textContent = "Complete";
    label.textContent = task.label || "Background task";
    title.append(badge, label);
    head.append(title, actionButton("Clear", () => {
      recent = recent.filter((candidate) => candidate.id !== task.id);
      write(RECENT_KEY, recent);
      render();
    }));

    const detail = document.createElement("small");
    detail.textContent = "Finished successfully.";
    row.append(head, detail);
    return row;
  };

  const failureRow = (task) => {
    const row = document.createElement("div");
    row.className = "task-row task-notification task-failed";

    const head = document.createElement("div");
    head.className = "task-notification-head";
    const title = document.createElement("span");
    const badge = document.createElement("b");
    const label = document.createElement("strong");
    const actions = document.createElement("span");
    const detail = document.createElement("p");

    badge.className = "task-state-badge failed";
    badge.textContent = "Failed";
    label.textContent = task.label || "Background task";
    title.append(badge, label);

    actions.className = "task-row-actions";
    detail.className = "task-failure-detail";
    detail.hidden = true;
    detail.textContent = task.detail || "The task stopped unexpectedly.";

    const detailsButton = actionButton("Details", () => {
      detail.hidden = !detail.hidden;
      detailsButton.textContent = detail.hidden ? "Details" : "Hide details";
      detailsButton.setAttribute("aria-expanded", String(!detail.hidden));
    });
    detailsButton.setAttribute("aria-expanded", "false");

    actions.append(detailsButton, actionButton("Clear", () => {
      acknowledgements.add(failureSignature(task));
      write(FAILURE_ACK_KEY, [...acknowledgements].slice(-100));
      render();
    }));
    head.append(title, actions);

    const note = document.createElement("small");
    note.textContent = "This task needs attention.";
    const activityLink = document.createElement("a");
    activityLink.href = task.href || "/activity";
    activityLink.className = "task-activity-link";
    activityLink.textContent = "Open Activity";
    row.append(head, note, detail, activityLink);
    return row;
  };

  const scheduledTasksFooter = () => {
    if (!document.body.classList.contains("role-librarian")) return null;
    const footer = document.createElement("div");
    footer.className = "task-widget-footer";
    const link = document.createElement("a");
    link.className = "task-scheduled-link";
    link.href = "/settings/scheduled-tasks";
    link.textContent = "Scheduled Tasks";
    link.title = "Open scheduled task settings";
    footer.append(link);
    return footer;
  };

  const visibleFailures = () => failures.filter((task) => !acknowledgements.has(failureSignature(task)));
  const hasTaskContent = () => Boolean(active.length || recent.length || visibleFailures().length);

  const applyOpen = () => {
    widget.classList.toggle("visible", hasTaskContent());
    widget.classList.toggle("is-pinned", open);
    popover.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  };

  function render() {
    pruneRecent();
    const failed = visibleFailures();
    widget.classList.toggle("idle", !active.length);
    widget.classList.toggle("has-attention", !active.length && Boolean(recent.length) && !failed.length);
    widget.classList.toggle("has-failure", Boolean(failed.length));

    if (failed.length) {
      summary.textContent = failed.length === 1 ? "1 task failed" : `${failed.length} tasks failed`;
      cardDetail.textContent = "Open for details";
      toggle.setAttribute("aria-label", `${summary.textContent}. Open task notifications.`);
    } else if (active.length) {
      summary.textContent = active.length === 1 ? active[0].label : `${active.length} tasks running`;
      cardDetail.textContent = active.length === 1
        ? (active[0].detail || "Working in the background")
        : "Open for task details";
      toggle.setAttribute("aria-label", summary.textContent);
    } else if (recent.length) {
      summary.textContent = recent.length === 1 ? "Task complete" : `${recent.length} tasks completed`;
      cardDetail.textContent = "Completed tasks remain here for 10 minutes";
      toggle.setAttribute("aria-label", `${summary.textContent}. Open task notifications.`);
    } else {
      summary.textContent = "No tasks currently active";
      cardDetail.textContent = "Open task center";
      toggle.setAttribute("aria-label", "No tasks currently active. Open task center.");
    }

    const rows = [
      ...active.map(activeRow),
      ...failed.map(failureRow),
      ...recent.slice().sort((a, b) => b.createdAt - a.createdAt).map(completeRow),
    ];

    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "task-empty";
      empty.textContent = "No Tasks Currently Active";
      rows.push(empty);
    }

    const footer = scheduledTasksFooter();
    if (footer) rows.push(footer);
    list.replaceChildren(...rows);
    applyOpen();
  }

  const finish = (task) => setTimeout(async () => {
    try {
      const response = await fetch("/api/task-failures", {cache: "no-store"});
      if (response.ok) {
        const data = await response.json();
        failures = Array.isArray(data.failures) ? data.failures : [];
      }
    } catch (_error) {}

    if (active.some((candidate) => candidate.id === task.id)
        || visibleFailures().some((candidate) => candidate.id === task.id)) {
      render();
      return;
    }

    const now = Date.now();
    recent = recent.filter((candidate) => candidate.id !== task.id);
    recent.push({
      id: task.id,
      label: task.label,
      createdAt: now,
      expiresAt: now + COMPLETE_TTL_MS,
    });
    write(RECENT_KEY, recent);
    render();
  }, 1800);

  const accept = (incoming) => {
    const next = (Array.isArray(incoming) ? incoming : [])
      .filter((task) => task?.id && task.id !== "media-fingerprints-queued");
    const nextMap = new Map(next.map((task) => [task.id, task]));

    for (const [id, task] of previous) {
      if (!nextMap.has(id)) finish(task);
    }

    let changed = false;
    for (const task of next) {
      for (const key of [...acknowledgements]) {
        if (key.startsWith(`${task.id}|`)) {
          acknowledgements.delete(key);
          changed = true;
        }
      }
    }
    if (changed) write(FAILURE_ACK_KEY, [...acknowledgements]);

    active = next;
    previous = nextMap;
    queueMicrotask(render);
  };

  document.addEventListener("infomancer:tasks", (event) => accept(event.detail?.tasks || []));

  const sync = async () => {
    try {
      const response = await fetch("/api/tasks", {cache: "no-store"});
      if (response.ok) {
        const data = await response.json();
        accept(data.tasks || []);
      }
    } catch (_error) {}
  };

  const pollFailures = async () => {
    try {
      const response = await fetch("/api/task-failures", {cache: "no-store"});
      if (response.ok) {
        const data = await response.json();
        failures = Array.isArray(data.failures) ? data.failures : [];
        render();
      }
    } catch (_error) {}
    setTimeout(pollFailures, active.length ? 1800 : 4000);
  };

  toggle.addEventListener("click", (event) => {
    event.preventDefault();
    open = !open;
    applyOpen();
  });

  document.getElementById("task-minimize")?.addEventListener("click", (event) => {
    event.stopPropagation();
    open = false;
    applyOpen();
  });

  document.getElementById("task-dismiss")?.addEventListener("click", (event) => {
    event.stopPropagation();
    open = false;
    applyOpen();
  });

  document.addEventListener("click", (event) => {
    if (!open || widget.contains(event.target)) return;
    open = false;
    applyOpen();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !open) return;
    open = false;
    applyOpen();
    toggle.focus();
  });

  new MutationObserver(() => {
    if (open && popover.hidden) queueMicrotask(applyOpen);
  }).observe(popover, {attributes: true, attributeFilter: ["hidden"]});

  setInterval(() => {
    pruneRecent();
    if (recent.length || visibleFailures().length) render();
  }, 15000);

  pruneRecent();
  render();
  sync();
  pollFailures();
})();
