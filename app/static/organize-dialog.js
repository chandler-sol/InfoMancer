(() => {
  const dialog = document.getElementById("organize-dialog");
  const body = document.getElementById("organize-dialog-body");
  if (!dialog || !body || typeof dialog.showModal !== "function") return;

  let opener = null;
  const dialogPath = /^\/titles\/(?:\d+\/(?:organize|libraries)|sort-titles)$/;
  let draggedSortRow = null;
  const sortRowAnimations = new WeakMap();

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
      if (Math.abs(x) < 1 && Math.abs(y) < 1) return;
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
      dialog.classList.remove("closing", "loading");
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

  const openDialog = async (url, trigger) => {
    opener = trigger;
    dialog.classList.add("loading");
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch(url, {headers: {"X-Requested-With": "InfoMancerDialog"}});
      if (!response.ok || !(await renderResponse(response))) window.location.assign(url);
    } catch (_) {
      window.location.assign(url);
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

  document.addEventListener("click", (event) => {
    const close = event.target.closest("[data-organize-close]");
    if (close && dialog.contains(close)) {
      event.preventDefault();
      closeDialog();
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
    const link = event.target.closest('a[href*="/organize"]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || !dialogPath.test(url.pathname)) return;
    event.preventDefault();
    link.closest("details")?.removeAttribute("open");
    openDialog(url.href, link);
  });

  document.addEventListener("infomancer:open-dialog", (event) => {
    if (event.detail?.url) openDialog(event.detail.url, event.detail.trigger || null);
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
    const form = event.target.closest("form.organize-title-form");
    if (!form) return;
    event.preventDefault();
    const submitter = event.submitter;
    submitter?.setAttribute("disabled", "");
    dialog.classList.add("loading");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: {"X-Requested-With": "InfoMancerDialog"},
      });
      const destination = new URL(response.url, window.location.href);
      if (dialogPath.test(destination.pathname)) {
        await renderResponse(response);
        return;
      }
      const message = destination.searchParams.get("message") || "Organization saved.";
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
