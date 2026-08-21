(() => {
  const searchShell = document.querySelector("[data-collection-search-shell]");
  const grid = document.querySelector("[data-collection-grid]");
  const toolbar = document.querySelector("[data-collection-order-toolbar]");
  const collectionId = searchShell?.dataset.collectionId || "";
  const csrfToken = searchShell?.dataset.csrfToken || "";

  const requestHeaders = (extra = {}) => {
    const headers = new Headers(extra);
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    return headers;
  };

  if (searchShell && collectionId) {
    const form = searchShell.querySelector("[data-collection-search-form]");
    const input = form?.querySelector('input[name="q"]');
    const results = searchShell.querySelector("[data-collection-search-results]");
    const state = searchShell.querySelector("[data-collection-search-state]");
    let searchController = null;
    let debounceTimer = 0;

    const renderResults = (items, query) => {
      if (!results) return;
      results.replaceChildren();
      results.hidden = false;
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "collection-search-empty";
        empty.textContent = `No available library titles match “${query}”. Try another title or fewer words.`;
        results.append(empty);
        return;
      }

      items.forEach((item) => {
        const addForm = document.createElement("form");
        addForm.method = "post";
        addForm.action = `/collections/${collectionId}/titles`;

        const csrf = document.createElement("input");
        csrf.type = "hidden";
        csrf.name = "csrf_token";
        csrf.value = csrfToken;
        addForm.append(csrf);

        const titleId = document.createElement("input");
        titleId.type = "hidden";
        titleId.name = "title_id";
        titleId.value = String(item.id);
        addForm.append(titleId);

        const identity = document.createElement("span");
        if (item.poster_url) {
          const poster = document.createElement("img");
          poster.src = item.poster_url;
          poster.alt = "";
          poster.loading = "lazy";
          identity.append(poster);
        } else {
          const placeholder = document.createElement("span");
          placeholder.className = "collection-search-result-placeholder";
          placeholder.textContent = String(item.display_title || "?").slice(0, 1).toUpperCase();
          identity.append(placeholder);
        }

        const title = document.createElement("strong");
        title.textContent = item.display_title;
        identity.append(title);

        const meta = document.createElement("small");
        meta.textContent = `${item.kind === "tv" ? "TV series" : "Movie"}${item.display_year ? ` · ${item.display_year}` : ""}`;
        identity.append(meta);
        addForm.append(identity);

        const add = document.createElement("button");
        add.className = "button small";
        add.textContent = "Add";
        addForm.append(add);
        results.append(addForm);
      });
    };

    const runSearch = async (rawQuery) => {
      const query = rawQuery.trim();
      searchController?.abort();
      if (!query) {
        if (results) {
          results.replaceChildren();
          results.hidden = true;
        }
        if (state) state.textContent = "";
        return;
      }

      searchController = new AbortController();
      if (state) state.textContent = "Searching your Library…";
      try {
        const response = await fetch(
          `/api/collections/${collectionId}/search?q=${encodeURIComponent(query)}`,
          {
            credentials: "same-origin",
            cache: "no-store",
            signal: searchController.signal,
            headers: requestHeaders({"Accept": "application/json"}),
          },
        );
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Search failed (${response.status})`);
        renderResults(data.results || [], data.query || query);
        if (state) {
          const count = (data.results || []).length;
          state.textContent = count
            ? `${count} available title${count === 1 ? "" : "s"} found.`
            : "";
        }
      } catch (error) {
        if (error?.name === "AbortError") return;
        if (state) state.textContent = error.message || "Library search could not be completed.";
      }
    };

    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch(input?.value || "");
    });

    input?.addEventListener("input", () => {
      window.clearTimeout(debounceTimer);
      const query = input.value.trim();
      if (!query) {
        runSearch("");
        return;
      }
      debounceTimer = window.setTimeout(() => runSearch(query), 240);
    });

    if (input?.value.trim()) runSearch(input.value);
  }

  if (!grid || !toolbar || !collectionId) return;

  const toggle = toolbar.querySelector("[data-collection-reorder-toggle]");
  const cancel = toolbar.querySelector("[data-collection-reorder-cancel]");
  const save = toolbar.querySelector("[data-collection-reorder-save]");
  const status = toolbar.querySelector("[data-collection-order-status]");
  const help = toolbar.querySelector("[data-collection-order-help]");
  let originalOrder = [];
  let draggedCard = null;

  const cards = () => [...grid.querySelectorAll("[data-collection-item]")];
  const currentOrder = () => cards().map((card) => card.dataset.collectionItem);
  const sameOrder = (left, right) => (
    left.length === right.length && left.every((value, index) => value === right[index])
  );

  const updateControls = () => {
    const orderedCards = cards();
    orderedCards.forEach((card, index) => {
      const number = card.querySelector("[data-collection-order-number]");
      if (number) number.textContent = String(index + 1);
      const earlier = card.querySelector('[data-collection-move="earlier"]');
      const later = card.querySelector('[data-collection-move="later"]');
      if (earlier) earlier.disabled = index === 0;
      if (later) later.disabled = index === orderedCards.length - 1;
    });
    if (save) save.disabled = sameOrder(currentOrder(), originalOrder);
  };

  const setReordering = (enabled) => {
    grid.classList.toggle("is-reordering", enabled);
    cards().forEach((card) => {
      card.draggable = enabled;
    });
    if (toggle) toggle.hidden = enabled;
    if (cancel) cancel.hidden = !enabled;
    if (save) save.hidden = !enabled;
    if (help) {
      help.textContent = enabled
        ? "Drag covers into place, or use the arrow buttons. Changes are not saved until you choose Save order."
        : "Items are shown in your saved manual order.";
    }
    if (status) {
      status.textContent = "";
      status.classList.remove("error");
    }
    updateControls();
  };

  const restoreOriginalOrder = () => {
    const byToken = new Map(cards().map((card) => [card.dataset.collectionItem, card]));
    originalOrder.forEach((token) => {
      const card = byToken.get(token);
      if (card) grid.append(card);
    });
    updateControls();
  };

  toggle?.addEventListener("click", () => {
    originalOrder = currentOrder();
    setReordering(true);
  });

  cancel?.addEventListener("click", () => {
    restoreOriginalOrder();
    setReordering(false);
  });

  grid.addEventListener("click", (event) => {
    if (!grid.classList.contains("is-reordering")) return;
    const move = event.target.closest("[data-collection-move]");
    if (move) {
      event.preventDefault();
      const card = move.closest("[data-collection-item]");
      if (!card) return;
      if (move.dataset.collectionMove === "earlier" && card.previousElementSibling?.matches("[data-collection-item]")) {
        card.previousElementSibling.before(card);
      } else if (move.dataset.collectionMove === "later" && card.nextElementSibling?.matches("[data-collection-item]")) {
        card.nextElementSibling.after(card);
      }
      updateControls();
      return;
    }
    if (event.target.closest(".cover-card-link")) event.preventDefault();
  });

  grid.addEventListener("dragstart", (event) => {
    if (!grid.classList.contains("is-reordering")) return;
    const card = event.target.closest("[data-collection-item]");
    if (!card) return;
    draggedCard = card;
    card.classList.add("collection-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.collectionItem || "");
  });

  grid.addEventListener("dragover", (event) => {
    if (!draggedCard || !grid.classList.contains("is-reordering")) return;
    const target = event.target.closest("[data-collection-item]");
    if (!target || target === draggedCard) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    cards().forEach((card) => card.classList.remove("collection-drop-target"));
    target.classList.add("collection-drop-target");
    const box = target.getBoundingClientRect();
    const before = event.clientX < box.left + box.width / 2;
    if (before) target.before(draggedCard);
    else target.after(draggedCard);
    updateControls();
  });

  grid.addEventListener("drop", (event) => {
    if (!draggedCard) return;
    event.preventDefault();
    cards().forEach((card) => card.classList.remove("collection-drop-target"));
    updateControls();
  });

  grid.addEventListener("dragend", () => {
    cards().forEach((card) => card.classList.remove("collection-dragging", "collection-drop-target"));
    draggedCard = null;
    updateControls();
  });

  save?.addEventListener("click", async () => {
    const order = currentOrder();
    if (sameOrder(order, originalOrder)) return;
    save.disabled = true;
    if (cancel) cancel.disabled = true;
    if (status) {
      status.classList.remove("error");
      status.textContent = "Saving order…";
    }

    const payload = new FormData();
    order.forEach((token) => payload.append("order", token));
    try {
      const response = await fetch(`/collections/${collectionId}/reorder`, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        body: payload,
        headers: requestHeaders({"Accept": "application/json"}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Save failed (${response.status})`);
      originalOrder = order;
      if (status) status.textContent = data.detail || "Collection order saved.";
      window.setTimeout(() => setReordering(false), 550);
    } catch (error) {
      if (status) {
        status.classList.add("error");
        status.textContent = error.message || "Collection order could not be saved.";
      }
      save.disabled = false;
    } finally {
      if (cancel) cancel.disabled = false;
    }
  });
})();
