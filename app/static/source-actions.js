(() => {
  const forms = [
    ...document.querySelectorAll('form[action="/scan-all"]'),
    ...document.querySelectorAll('.source-action-rail form[action$="/scan"], .source-action-rail form[action$="/check"]'),
  ];
  if (!forms.length || typeof window.fetch !== "function") return;

  const pageHead = document.querySelector(".sources-page-head");
  const feedback = document.createElement("div");
  feedback.className = "source-live-status";
  feedback.setAttribute("role", "status");
  feedback.setAttribute("aria-live", "polite");
  feedback.hidden = true;
  pageHead?.insertAdjacentElement("afterend", feedback);

  let feedbackTimer = 0;
  const showFeedback = (message, state = "working", linger = 4200) => {
    window.clearTimeout(feedbackTimer);
    feedback.dataset.state = state;
    feedback.textContent = message;
    feedback.hidden = false;
    if (linger > 0) {
      feedbackTimer = window.setTimeout(() => {
        feedback.hidden = true;
      }, linger);
    }
  };

  const actionPath = (form) => new URL(form.action, window.location.origin).pathname;
  const sourceName = (form) => (
    form.closest(".root-row")?.querySelector(".root-library-link strong")?.textContent?.trim()
    || "source"
  );
  const actionKind = (form) => {
    const path = actionPath(form);
    if (path === "/scan-all") return "scan-all";
    if (path.endsWith("/check")) return "check";
    return "scan";
  };

  const sourceRows = [...document.querySelectorAll(".root-row")];
  const rowForSourceId = (sourceId) => sourceRows.find((row) => (
    row.querySelector(".root-library-link[data-source-id]")?.dataset.sourceId === String(sourceId)
  ));
  const rowForSourceLabel = (label) => sourceRows.find((row) => (
    row.querySelector(".root-library-link strong")?.textContent?.trim() === label
  ));
  const rowForScanAllTask = (task) => {
    const detail = String(task?.detail || "");
    const separator = " · ";
    if (!detail.includes(separator)) return null;
    return rowForSourceLabel(detail.slice(detail.lastIndexOf(separator) + separator.length).trim());
  };
  const setRowScanState = (row, active, detail = "") => {
    if (!row) return;
    const status = row.querySelector("[data-source-scan-status]");
    const scanButton = row.querySelector('.source-action-rail form[action$="/scan"] button');
    row.classList.toggle("source-row-scanning", active);
    row.dataset.scanActive = active ? "1" : "0";

    if (status) {
      if (active) {
        const copy = String(detail || "").trim();
        status.textContent = copy
          ? (copy.toLowerCase().startsWith("scanning") ? copy : `Scanning · ${copy}`)
          : "Scanning…";
        status.hidden = false;
      } else {
        status.hidden = true;
      }
    }

    if (scanButton) {
      if (!scanButton.dataset.sourceScanIdleText) {
        scanButton.dataset.sourceScanIdleText = scanButton.textContent.trim() || "Scan";
      }
      if (active) {
        scanButton.disabled = true;
        scanButton.textContent = "Scanning…";
      } else if (scanButton.dataset.sourceScanIdleText) {
        scanButton.disabled = false;
        scanButton.textContent = scanButton.dataset.sourceScanIdleText;
      }
    }
  };

  sourceRows.forEach((row) => {
    if (row.dataset.scanActive !== "1") return;
    const existing = row.querySelector("[data-source-scan-status]")?.textContent?.trim() || "Scanning…";
    setRowScanState(row, true, existing);
  });

  // The task center already owns background-task polling. Observe its task snapshots
  // instead of adding a second poller to Sources, then refresh the server-rendered
  // counts and health state once a source scan disappears from the active task list.
  const isSourceScanTaskId = (id) => id === "scan-all" || /^scan-\d+$/.test(id);
  const scanTaskIdFor = (form, kind) => {
    if (kind === "scan-all") return "scan-all";
    if (kind !== "scan") return "";
    const match = actionPath(form).match(/^\/roots\/(\d+)\/scan$/);
    return match ? `scan-${match[1]}` : "";
  };
  const observedScanTasks = new Set();
  document.querySelectorAll('.root-row[data-scan-active="1"] .root-library-link[data-source-id]').forEach((link) => {
    if (link.dataset.sourceId) observedScanTasks.add(`scan-${link.dataset.sourceId}`);
  });
  let scanRefreshScheduled = false;
  const scheduleScanRefresh = () => {
    if (scanRefreshScheduled) return;
    scanRefreshScheduled = true;
    showFeedback("Scan complete. Refreshing source totals…", "success", 0);
    window.setTimeout(() => window.location.reload(), 250);
  };
  const rememberScanTask = (form, kind) => {
    const taskId = scanTaskIdFor(form, kind);
    if (taskId) observedScanTasks.add(taskId);
  };
  document.addEventListener("infomancer:tasks", (event) => {
    const tasks = Array.isArray(event.detail?.tasks) ? event.detail.tasks : [];
    const scanTasks = tasks.filter((task) => isSourceScanTaskId(String(task?.id || "")));
    const currentScanTasks = new Set(scanTasks.map((task) => String(task?.id || "")));
    const activeRows = new Set();

    scanTasks.forEach((task) => {
      const taskId = String(task?.id || "");
      let row = null;
      let detail = String(task?.detail || "");
      if (taskId === "scan-all") {
        row = rowForScanAllTask(task);
        detail = row ? "Scanning now · Scan all in progress" : detail;
      } else {
        const sourceId = taskId.replace(/^scan-/, "");
        row = rowForSourceId(sourceId);
      }
      if (row) {
        activeRows.add(row);
        setRowScanState(row, true, detail);
      }
    });

    sourceRows.forEach((row) => {
      if (row.classList.contains("source-row-scanning") && !activeRows.has(row)) {
        setRowScanState(row, false);
      }
    });

    for (const taskId of observedScanTasks) {
      if (!currentScanTasks.has(taskId)) {
        scheduleScanRefresh();
        return;
      }
    }
    currentScanTasks.forEach((taskId) => observedScanTasks.add(taskId));
  });

  const optimisticTaskWidget = (kind, label) => {
    if (kind === "check") return;
    const widget = document.getElementById("task-widget");
    const toggle = document.getElementById("task-widget-toggle");
    const summary = document.getElementById("task-summary");
    const detail = document.getElementById("task-card-detail");
    if (!widget || !toggle || !summary || !detail) return;

    const taskLabel = kind === "scan-all" ? "Scanning all sources" : `Scanning ${label}`;
    widget.classList.remove("idle", "has-attention");
    widget.classList.add("visible");
    summary.textContent = taskLabel;
    detail.textContent = "Starting in the background…";
    toggle.setAttribute("aria-label", taskLabel);
  };

  const freshFormFor = (documentRoot, path) => (
    [...documentRoot.querySelectorAll(".source-action-rail form")]
      .find((candidate) => candidate.getAttribute("action") === path)
  );

  const refreshConnectionState = (documentRoot, form) => {
    const path = actionPath(form);
    const currentRow = form.closest(".root-row");
    const freshRow = freshFormFor(documentRoot, path)?.closest(".root-row");
    if (!currentRow || !freshRow) return;

    const currentHealth = currentRow.querySelector(".source-health");
    const freshHealth = freshRow.querySelector(".source-health");
    if (currentHealth && freshHealth) {
      currentHealth.className = freshHealth.className;
      currentHealth.textContent = freshHealth.textContent;
    }

    currentRow.querySelector(".source-health-guidance")?.remove();
    const freshGuidance = freshRow.querySelector(".source-health-guidance");
    if (freshGuidance && currentHealth) {
      currentHealth.insertAdjacentElement("afterend", freshGuidance.cloneNode(true));
    }
  };

  const flashMessageFrom = (documentRoot) => (
    documentRoot.querySelector("#flash-message span")?.textContent?.trim() || ""
  );

  const resetButton = (button, originalText) => {
    button.disabled = false;
    button.classList.remove("source-action-busy");
    button.textContent = originalText;
  };

  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (form.dataset.sourceSubmitting === "1") {
        event.preventDefault();
        return;
      }
      event.preventDefault();

      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (!button) return;
      const originalText = button.textContent;
      const kind = actionKind(form);
      const label = sourceName(form);
      const row = form.closest(".root-row");
      form.dataset.sourceSubmitting = "1";
      button.disabled = true;
      button.classList.add("source-action-busy");
      row?.classList.add("source-row-working");

      if (kind === "scan-all") {
        button.textContent = "Starting…";
        showFeedback("Starting a scan of all sources…", "working", 0);
      } else if (kind === "scan") {
        button.textContent = "Starting…";
        showFeedback(`Starting a scan of ${label}…`, "working", 0);
      } else {
        button.textContent = "Checking…";
        showFeedback(`Checking the connection to ${label}…`, "working", 0);
      }

      try {
        const csrfToken = form.querySelector('input[name="csrf_token"]')?.value || "";
        const headers = {"X-Requested-With": "InfoMancerAsync"};
        if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          redirect: "follow",
          headers,
        });
        if (!response.ok) {
          throw new Error(`InfoMancer returned HTTP ${response.status}.`);
        }

        const html = await response.text();
        const freshDocument = new DOMParser().parseFromString(html, "text/html");
        if (!freshDocument.querySelector(".sources-page-head")) {
          throw new Error("The source action completed, but InfoMancer could not refresh its status.");
        }

        if (kind === "check") refreshConnectionState(freshDocument, form);
        else {
          rememberScanTask(form, kind);
          optimisticTaskWidget(kind, label);
          if (kind === "scan") setRowScanState(row, true, "Starting…");
        }

        const serverMessage = flashMessageFrom(freshDocument);
        const fallbackMessage = kind === "check"
          ? `Connection check completed for ${label}.`
          : kind === "scan-all"
            ? "Source scan started. You can keep using InfoMancer while it runs."
            : `Scan started for ${label}. You can keep using InfoMancer while it runs.`;
        showFeedback(serverMessage || fallbackMessage, "success");

        if (kind !== "check") {
          if (kind === "scan-all") {
            button.textContent = "Started";
            window.setTimeout(() => resetButton(button, originalText), 900);
          }
        } else {
          resetButton(button, originalText);
        }
      } catch (error) {
        showFeedback(
          error?.message || "InfoMancer could not start that source action.",
          "error",
          6500,
        );
        if (kind === "scan") setRowScanState(row, false);
        resetButton(button, originalText);
      } finally {
        form.dataset.sourceSubmitting = "0";
        row?.classList.remove("source-row-working");
      }
    });
  });
})();

(() => {
  const editors = [...document.querySelectorAll(".root-name-editor")];
  const closeEditors = (except = null) => {
    editors.forEach((editor) => {
      if (editor !== except) editor.removeAttribute("open");
    });
  };

  editors.forEach((editor) => {
    editor.addEventListener("toggle", () => {
      if (editor.open) closeEditors(editor);
    });
  });

  document.querySelectorAll("[data-cancel-root-name]").forEach((button) => {
    button.addEventListener("click", () => button.closest("details")?.removeAttribute("open"));
  });

  document.addEventListener("click", (event) => {
    editors.forEach((editor) => {
      if (editor.open && !editor.contains(event.target)) editor.removeAttribute("open");
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeEditors();
  });

  document.querySelectorAll(".root-library-link[data-source-id]").forEach((link) => {
    const key = `infomancer-source-opened:${link.dataset.sourceId}`;
    if (sessionStorage.getItem(key)) link.classList.add("session-opened");
    link.addEventListener("click", () => sessionStorage.setItem(key, "1"));
  });
})();
