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
        else optimisticTaskWidget(kind, label);

        const serverMessage = flashMessageFrom(freshDocument);
        const fallbackMessage = kind === "check"
          ? `Connection check completed for ${label}.`
          : kind === "scan-all"
            ? "Source scan started. You can keep using InfoMancer while it runs."
            : `Scan started for ${label}. You can keep using InfoMancer while it runs.`;
        showFeedback(serverMessage || fallbackMessage, "success");

        if (kind !== "check") {
          button.textContent = "Started";
          window.setTimeout(() => resetButton(button, originalText), 900);
        } else {
          resetButton(button, originalText);
        }
      } catch (error) {
        showFeedback(
          error?.message || "InfoMancer could not start that source action.",
          "error",
          6500,
        );
        resetButton(button, originalText);
      } finally {
        form.dataset.sourceSubmitting = "0";
        row?.classList.remove("source-row-working");
      }
    });
  });
})();
