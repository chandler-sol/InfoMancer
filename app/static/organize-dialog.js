(() => {
  const dialog = document.getElementById("organize-dialog");
  const body = document.getElementById("organize-dialog-body");
  if (!dialog || !body || typeof dialog.showModal !== "function") return;

  let opener = null;
  const dialogPath = /^(?:\/titles\/(?:\d+\/(?:organize|libraries)|sort-titles|organize-bulk)|\/files\/\d+\/edition-version(?:\/preview)?)$/;
  let draggedSortRow = null;
  const sortRowAnimations = new WeakMap();

  const csrfToken = () => document.querySelector('input[name="csrf_token"]')?.value || "";
  const requestHeaders = (extra = {}) => {
    const headers = new Headers(extra);
    const token = csrfToken();
    if (token) headers.set("X-CSRF-Token", token);
    return headers;
  };

  const submitFallback = (url, method = "GET", body = null) => {
    if (String(method).toUpperCase() === "GET") {
      window.location.assign(url);
      return;
    }
    const form = document.createElement("form");
    form.method = "post";
    form.action = url;
    form.hidden = true;
    if (body instanceof FormData) {
      for (const [name, value] of body.entries()) {
        if (typeof value !== "string") continue;
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        form.append(input);
      }
    }
    const token = csrfToken();
    if (token && !form.querySelector('input[name="csrf_token"]')) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "csrf_token";
      input.value = token;
      form.append(input);
    }
    document.body.append(form);
    form.submit();
  };

  const sortRows = () => [...body.querySelectorAll("[data-sort-title-order] li")];
  const captureSortRowPositions = () => new Map(
    sortRows().map((row) => [row, row.getBoundingClientRect()]),
  );
  const animateSortRowPositions = (positions) => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    sortRows().forEach((row) => {
      if (row === draggedSortRow) return;
      const previous = positions.get(row);
      const current = row.getBoundingClientRect();
      if (!previous) return;
      const x = previous.left - current.left;
      const y = previous.top - current.top;
      if (Math.abs(x) < 1 || Math.abs(y) < 1) {
        if (Math.abs(x) < 1 && Math.abs(y) < 1) return;
      }
      sortRowAnimations.get(row)?.cancel();
      const animation = row.animate(
        [
          {transform: `translate(${x}px, ${y}px)`},
          {transform: "translate(0, 0)"},
        ],
        {duration: 300, easing: "cubic-bezier(.22, 1, .36, 1)"},
      );
      sortRowAnimations.set(row, animation);
    });
  };

  const closeDialog = () => {
    if (!dialog.open || dialog.classList.contains("closing")) return;
    dialog.classList.add("closing");
    window.setTimeout(() => {
      dialog.close();
      dialog.classList.remove("closing", "loading", "title-workflow-dialog");
      body.replaceChildren();
      opener?.focus();
    }, 350);
  };

  const renderResponse = async (response) => {
    const html = await response.text();
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const content = parsed.querySelector("[data-organize-content]");
    if (!content) return false;
    body.replaceChildren(document.importNode(content, true));
    body.scrollTop = 0;
    body.scrollLeft = 0;
    const heading = body.querySelector("h1");
    if (heading) heading.id = "organize-dialog-title";
    body.querySelector(".back")?.remove();
    body.querySelectorAll("a").forEach((link) => {
      if (link.textContent.trim() === "Cancel") {
        link.href = "#";
        link.dataset.organizeClose = "";
      }
    });
    dialog.classList.remove("loading");
    updateSortTitleOrder();
    return true;
  };

  const openDialog = async (url, trigger, options = {}) => {
    opener = trigger;
    dialog.classList.remove("title-workflow-dialog");
    dialog.classList.add("loading");
    if (!dialog.open) dialog.showModal();
    const method = String(options.method || "GET").toUpperCase();
    try {
      const parsedUrl = new URL(url, window.location.href);
      if (parsedUrl.origin !== window.location.origin || !dialogPath.test(parsedUrl.pathname)) {
        throw new Error("Unsupported dialog destination");
      }
      const response = await fetch(parsedUrl.href, {
        method,
        body: method === "GET" ? undefined : options.body,
        credentials: "same-origin",
        cache: "no-store",
        headers: requestHeaders({"X-Requested-With": "InfoMancerDialog"}),
      });
      if (!response.ok || !(await renderResponse(response))) {
        closeDialog();
        submitFallback(parsedUrl.href, method, options.body);
      }
    } catch (_) {
      closeDialog();
      submitFallback(url, method, options.body);
    }
  };

  const updateSortTitleOrder = (renumber = false) => {
    const prefix = body.querySelector('[name="prefix"]')?.value.trim() || "Prefix";
    const padded = body.querySelector('[name="number_style"]')?.value !== "plain";
    body.querySelectorAll("[data-sort-title-order] li").forEach((row, index, rows) => {
      const number = row.querySelector('[name="sequence_number"]');
      if (renumber && number) number.value = String(index + 1);
      const rawNumber = Math.max(1, Number(number?.value) || index + 1);
      const letter = row.querySelector('[name="sequence_letter"]')?.value || "";
      const output = row.querySelector("output");
      const formatted = padded
        ? String(rawNumber).padStart(Math.max(2, String(Math.max(rows.length, rawNumber)).length), "0")
        : String(rawNumber);
      if (output) output.textContent = `${prefix} ${formatted}${letter}`;
      row.querySelector('[data-sort-move="up"]')?.toggleAttribute("disabled", index === 0);
      row.querySelector('[data-sort-move="down"]')?.toggleAttribute("disabled", index === rows.length - 1);
    });
  };

  document.addEventListener("click", async (event) => {
    const close = event.target.closest("[data-organize-close]");
    if (close && dialog.contains(close)) {
      event.preventDefault();
      closeDialog();
      return;
    }

    const bulkFavorite = event.target.closest("[data-bulk-favorite-selected]");
    if (bulkFavorite) {
      event.preventDefault();
      const form = bulkFavorite.closest("form");
      const ids = [...new Set(
        [...(form?.querySelectorAll('input[name="selected"]') || [])]
          .map(input => input.value)
          .filter(value => /^\d+$/.test(value)),
      )];
      if (ids.length < 2) return;
      const status = form?.querySelector("[data-bulk-favorite-status]");
      const original = bulkFavorite.textContent;
      bulkFavorite.disabled = true;
      bulkFavorite.textContent = "Adding…";
      const requestBody = new FormData();
      ids.forEach(id => requestBody.append("selected", id));
      try {
        const response = await fetch("/titles/favorite-bulk", {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          body: requestBody,
          headers: requestHeaders({
            "Accept": "application/json",
            "X-InfoMancer-Async": "1",
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        bulkFavorite.textContent = "Added to Favorites ✓";
        if (status) status.textContent = data.detail || `Added ${ids.length} selected titles to Favorites.`;
        document.dispatchEvent(new CustomEvent("infomancer:bulk-favorite-complete", {
          detail: {titleIds: data.title_ids || ids, message: data.detail || "Favorites updated."},
        }));
      } catch (error) {
        bulkFavorite.disabled = false;
        bulkFavorite.textContent = original;
        if (status) status.textContent = error.message || "Selected titles could not be added to Favorites.";
      }
      return;
    }

    const move = event.target.closest("[data-sort-move]");
    if (move && dialog.contains(move)) {
      const row = move.closest("li");
      const positions = captureSortRowPositions();
      if (move.dataset.sortMove === "up" && row?.previousElementSibling) row.previousElementSibling.before(row);
      if (move.dataset.sortMove === "down" && row?.nextElementSibling) row.nextElementSibling.after(row);
      updateSortTitleOrder(true);
      animateSortRowPositions(positions);
      return;
    }
    const link = event.target.closest('a[data-organize-dialog],a[href*="/organize"]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || !dialogPath.test(url.pathname)) return;
    event.preventDefault();
    link.closest("details")?.removeAttribute("open");
    openDialog(url.href, link);
  });

  document.addEventListener("infomancer:open-dialog", (event) => {
    if (!event.detail?.url) return;
    openDialog(event.detail.url, event.detail.trigger || null, {
      method: event.detail.method || "GET",
      body: event.detail.body,
    });
  });

  body.addEventListener("dragstart", (event) => {
    if (!event.target.matches(".sort-title-drag")) return;
    draggedSortRow = event.target.closest("[data-sort-title-order] li");
    if (draggedSortRow) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", draggedSortRow.querySelector('[name="selected"]')?.value || "");
      draggedSortRow.classList.add("dragging");
      draggedSortRow.closest("[data-sort-title-order]")?.classList.add("is-dragging");
    }
  });
  body.addEventListener("dragover", (event) => {
    const order = event.target.closest("[data-sort-title-order]");
    if (!draggedSortRow || !order) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const target = event.target.closest("[data-sort-title-order] li");
    if (!target || target === draggedSortRow) return;
    const positions = captureSortRowPositions();
    const box = target.getBoundingClientRect();
    target.before(draggedSortRow);
    if (event.clientY > box.top + box.height / 2) target.after(draggedSortRow);
    animateSortRowPositions(positions);
  });
  body.addEventListener("dragend", () => {
    draggedSortRow?.classList.remove("dragging");
    draggedSortRow?.closest("[data-sort-title-order]")?.classList.remove("is-dragging");
    draggedSortRow = null;
    updateSortTitleOrder(true);
  });
  body.addEventListener("input", (event) => {
    if (event.target.matches('[name="prefix"],[name="sequence_number"]')) updateSortTitleOrder();
  });
  body.addEventListener("change", (event) => {
    if (event.target.matches('[name="number_style"],[name="sequence_letter"]')) updateSortTitleOrder();
  });

  body.addEventListener("submit", async (event) => {
    // title-detail-ux.js owns submissions while this shared shell is hosting a
    // title workflow. Without this boundary a Collections form would POST twice.
    if (dialog.classList.contains("title-workflow-dialog")) return;
    const form = event.target.closest("form.organize-title-form");
    if (!form) return;
    event.preventDefault();
    const submitter = event.submitter;
    submitter?.setAttribute("disabled", "");
    dialog.classList.add("loading");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        body: new FormData(form),
        headers: requestHeaders({"X-Requested-With": "InfoMancerDialog"}),
      });
      const destination = new URL(response.url, window.location.href);
      if (dialogPath.test(destination.pathname)) {
        await renderResponse(response);
        return;
      }
      const message = destination.searchParams.get("message") || "Organization saved.";
      if (form.matches("[data-organize-bulk]")) {
        closeDialog();
        document.dispatchEvent(new CustomEvent("infomancer:library-bulk-organized", {
          detail: {message},
        }));
        return;
      }
      const current = new URL(window.location.href);
      current.searchParams.set("message", message);
      window.location.assign(current.href);
    } catch (_) {
      form.submit();
    } finally {
      submitter?.removeAttribute("disabled");
    }
  });

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) closeDialog();
  });
})();
