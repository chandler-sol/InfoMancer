(() => {
  const grid = document.querySelector("[data-collection-grid]");
  const searchShell = document.querySelector("[data-collection-search-shell]");
  const toolbar = document.querySelector("[data-collection-order-toolbar]");
  const collectionId = searchShell?.dataset.collectionId || "";
  const csrfToken = (
    searchShell?.dataset.csrfToken
    || document.querySelector('input[name="csrf_token"]')?.value
    || ""
  );

  const requestHeaders = (extra = {}) => {
    const headers = new Headers(extra);
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    return headers;
  };

  const dialogs = [...document.querySelectorAll(".collection-management-dialog")];
  const closeDeleteConfirmations = (except = null) => {
    document.querySelectorAll(".collection-delete-confirm[open]").forEach((details) => {
      if (details !== except) details.open = false;
    });
  };

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-collection-dialog-open]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.collectionDialogOpen || "");
      if (dialog?.showModal) {
        event.preventDefault();
        closeDeleteConfirmations();
        dialog.showModal();
        window.setTimeout(() => dialog.querySelector("input:not([type=hidden]), textarea")?.focus(), 0);
      }
      return;
    }

    const closer = event.target.closest("[data-collection-dialog-close]");
    if (closer) {
      event.preventDefault();
      closeDeleteConfirmations();
      closer.closest("dialog")?.close();
      return;
    }

    const deleteCancel = event.target.closest("[data-collection-delete-cancel]");
    if (deleteCancel) {
      event.preventDefault();
      deleteCancel.closest(".collection-delete-confirm")?.removeAttribute("open");
      return;
    }

    const activeDelete = event.target.closest(".collection-delete-confirm");
    closeDeleteConfirmations(activeDelete);
  });

  dialogs.forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        closeDeleteConfirmations();
        dialog.close();
      }
    });
    dialog.addEventListener("close", () => closeDeleteConfirmations());
  });

  const sizeInput = document.querySelector("[data-collection-cover-size]");
  const sizeSmaller = document.querySelector("[data-collection-cover-smaller]");
  const sizeLarger = document.querySelector("[data-collection-cover-larger]");
  const coverSizeStorageKey = "infomancer.collectionCoverSize";

  const clampCoverSize = (value) => Math.min(300, Math.max(120, Math.round(value / 10) * 10));
  const applyCoverSize = (value, persist = true) => {
    if (!grid || !sizeInput) return;
    const requested = clampCoverSize(Number(value) || Number(sizeInput.value) || 180);
    const availableWidth = Math.max(120, Math.floor(grid.getBoundingClientRect().width || requested));
    const size = Math.min(requested, availableWidth);
    sizeInput.value = String(requested);
    grid.style.setProperty("--cover-size", `${size}px`);
    if (persist) {
      try { window.localStorage.setItem(coverSizeStorageKey, String(requested)); } catch (_) {}
    }
  };

  if (sizeInput) {
    let saved = "";
    try { saved = window.localStorage.getItem(coverSizeStorageKey) || ""; } catch (_) {}
    applyCoverSize(saved || sizeInput.value, false);
    sizeInput.addEventListener("input", () => applyCoverSize(sizeInput.value));
    sizeSmaller?.addEventListener("click", () => applyCoverSize(Number(sizeInput.value) - 10));
    sizeLarger?.addEventListener("click", () => applyCoverSize(Number(sizeInput.value) + 10));
    window.addEventListener("resize", () => applyCoverSize(sizeInput.value, false), {passive: true});
  }

  let floatingMenu = null;
  let activeMenuDetails = null;

  const removeFloatingMenu = ({closeDetails = false} = {}) => {
    floatingMenu?.remove();
    floatingMenu = null;
    if (closeDetails && activeMenuDetails) activeMenuDetails.open = false;
    activeMenuDetails = null;
  };

  const positionFloatingMenu = () => {
    if (!floatingMenu || !activeMenuDetails) return;
    const summary = activeMenuDetails.querySelector(":scope > summary");
    if (!summary) return;
    const rect = summary.getBoundingClientRect();
    const width = Math.min(285, window.innerWidth - 24);
    floatingMenu.style.width = `${width}px`;
    const measuredHeight = floatingMenu.getBoundingClientRect().height;
    const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12));
    const below = rect.bottom + 6;
    const top = below + measuredHeight <= window.innerHeight - 12
      ? below
      : Math.max(12, rect.top - measuredHeight - 6);
    floatingMenu.style.left = `${Math.round(left)}px`;
    floatingMenu.style.top = `${Math.round(top)}px`;
  };

  const showFloatingMenu = (details) => {
    const panel = details.querySelector(":scope > div");
    if (!panel) return;
    removeFloatingMenu({closeDetails: activeMenuDetails && activeMenuDetails !== details});
    activeMenuDetails = details;
    floatingMenu = document.createElement("div");
    floatingMenu.className = "collection-floating-menu";
    floatingMenu.setAttribute("role", "menu");
    floatingMenu.innerHTML = panel.innerHTML;
    document.body.append(floatingMenu);
    positionFloatingMenu();
  };

  document.addEventListener("click", (event) => {
    const summary = event.target.closest(".collection-cover-library .cover-row-menu > summary");
    if (summary) {
      const details = summary.closest(".cover-row-menu");
      window.setTimeout(() => {
        if (details?.open) showFloatingMenu(details);
        else if (activeMenuDetails === details) removeFloatingMenu();
      }, 0);
      return;
    }
    if (floatingMenu && !event.target.closest(".collection-floating-menu")) {
      removeFloatingMenu({closeDetails: true});
    }
  });
  window.addEventListener("resize", positionFloatingMenu);
  window.addEventListener("scroll", () => {
    if (floatingMenu) removeFloatingMenu({closeDetails: true});
  }, {passive: true});

  if (searchShell && collectionId && grid) {
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
        addForm.dataset.collectionAddResult = "";

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

    results?.addEventListener("submit", async (event) => {
      const addForm = event.target.closest("form[data-collection-add-result]");
      if (!addForm) return;
      event.preventDefault();
      const button = event.submitter || addForm.querySelector("button");
      const originalText = button?.textContent || "Add";
      if (button) {
        button.disabled = true;
        button.textContent = "Adding…";
      }
      try {
        const response = await fetch(addForm.action, {
          method: "POST",
          credentials: "same-origin",
          cache: "no-store",
          body: new FormData(addForm),
          headers: requestHeaders({"X-Requested-With": "InfoMancerCollection"}),
        });
        if (!response.ok) throw new Error(`Add failed (${response.status})`);
        const html = await response.text();
        const parsed = new DOMParser().parseFromString(html, "text/html");
        const freshGrid = parsed.querySelector("[data-collection-grid]");
        if (!freshGrid) throw new Error("The collection did not return an updated cover grid.");
        removeFloatingMenu({closeDetails: true});
        grid.innerHTML = freshGrid.innerHTML;
        if (sizeInput) applyCoverSize(sizeInput.value, false);

        const currentMeta = document.querySelector("[data-collection-count-meta]");
        const freshMeta = parsed.querySelector("[data-collection-count-meta]");
        if (currentMeta && freshMeta) currentMeta.innerHTML = freshMeta.innerHTML;
        const currentArt = document.querySelector(".collection-detail-art");
        const freshArt = parsed.querySelector(".collection-detail-art");
        if (currentArt && freshArt) currentArt.innerHTML = freshArt.innerHTML;

        if (state) state.textContent = "Added to collection. You can keep searching for more titles.";
        await runSearch(input?.value || "");
      } catch (error) {
        if (state) state.textContent = error.message || "That title could not be added.";
        if (button) {
          button.disabled = false;
          button.textContent = originalText;
        }
      }
    });
  }

  if (!grid || !toolbar || !collectionId) return;

  const toggle = document.querySelector("[data-collection-reorder-toggle]");
  const cancel = toolbar.querySelector("[data-collection-reorder-cancel]");
  const save = toolbar.querySelector("[data-collection-reorder-save]");
  const status = toolbar.querySelector("[data-collection-order-status]");
  const help = toolbar.querySelector("[data-collection-order-help]");
  const managementButtons = [...document.querySelectorAll("[data-collection-dialog-open]")];
  let originalOrder = [];
  let draggedCard = null;
  let dragCandidate = null;
  let dragPointerId = null;
  let dragStartX = 0;
  let dragStartY = 0;
  const reorderAnimations = new WeakMap();

  const cards = () => [...grid.querySelectorAll("[data-collection-item]")];
  const currentOrder = () => cards().map((card) => card.dataset.collectionItem);
  const sameOrder = (left, right) => (
    left.length === right.length && left.every((value, index) => value === right[index])
  );
  const capturePositions = () => new Map(
    cards().map((card) => [card, card.getBoundingClientRect()]),
  );
  const animatePositions = (positions) => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    cards().forEach((card) => {
      if (card === draggedCard) return;
      const previous = positions.get(card);
      if (!previous) return;
      const current = card.getBoundingClientRect();
      const x = previous.left - current.left;
      const y = previous.top - current.top;
      if (Math.abs(x) < 1 && Math.abs(y) < 1) return;
      reorderAnimations.get(card)?.cancel();
      const animation = card.animate(
        [
          {transform: `translate(${x}px, ${y}px)`},
          {transform: "translate(0, 0)"},
        ],
        {duration: 240, easing: "cubic-bezier(.22, 1, .36, 1)"},
      );
      reorderAnimations.set(card, animation);
    });
  };

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

  const finishPointerDrag = (pointerId = null) => {
    if (draggedCard) {
      draggedCard.classList.remove("collection-dragging", "is-pointer-dragging");
      draggedCard.style.pointerEvents = "";
    }
    cards().forEach((card) => card.classList.remove("collection-drop-target"));
    if (pointerId !== null && grid.hasPointerCapture?.(pointerId)) {
      try { grid.releasePointerCapture(pointerId); } catch (_) {}
    }
    draggedCard = null;
    dragCandidate = null;
    dragPointerId = null;
    updateControls();
  };

  const setReordering = (enabled) => {
    dialogs.forEach((dialog) => { if (dialog.open) dialog.close(); });
    closeDeleteConfirmations();
    removeFloatingMenu({closeDetails: true});
    if (!enabled) finishPointerDrag(dragPointerId);
    grid.classList.toggle("is-reordering", enabled);
    toolbar.hidden = !enabled;
    if (toggle) toggle.hidden = enabled;
    managementButtons.forEach((button) => { button.disabled = enabled; });
    if (help) {
      help.textContent = "Drag covers into place or use the arrow controls. Changes are saved only when you choose Save order.";
    }
    if (status) {
      status.textContent = "";
      status.classList.remove("error");
    }
    updateControls();
  };

  const restoreOriginalOrder = () => {
    const positions = capturePositions();
    const byToken = new Map(cards().map((card) => [card.dataset.collectionItem, card]));
    originalOrder.forEach((token) => {
      const card = byToken.get(token);
      if (card) grid.append(card);
    });
    updateControls();
    animatePositions(positions);
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
      const positions = capturePositions();
      if (move.dataset.collectionMove === "earlier" && card.previousElementSibling?.matches("[data-collection-item]")) {
        card.previousElementSibling.before(card);
      } else if (move.dataset.collectionMove === "later" && card.nextElementSibling?.matches("[data-collection-item]")) {
        card.nextElementSibling.after(card);
      }
      updateControls();
      animatePositions(positions);
      return;
    }
    if (event.target.closest(".cover-card-link")) event.preventDefault();
  });

  grid.addEventListener("pointerdown", (event) => {
    if (!grid.classList.contains("is-reordering")) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (event.target.closest("[data-collection-move]")) return;
    const card = event.target.closest("[data-collection-item]");
    if (!card) return;
    dragCandidate = card;
    dragPointerId = event.pointerId;
    dragStartX = event.clientX;
    dragStartY = event.clientY;
  });

  grid.addEventListener("pointermove", (event) => {
    if (!dragCandidate || event.pointerId !== dragPointerId) return;
    const distance = Math.hypot(event.clientX - dragStartX, event.clientY - dragStartY);
    if (!draggedCard && distance < 6) return;

    if (!draggedCard) {
      draggedCard = dragCandidate;
      draggedCard.classList.add("collection-dragging", "is-pointer-dragging");
      draggedCard.style.pointerEvents = "none";
      try { grid.setPointerCapture(event.pointerId); } catch (_) {}
    }

    event.preventDefault();
    const hit = document.elementFromPoint(event.clientX, event.clientY);
    const target = hit?.closest("[data-collection-item]");
    if (!target || target === draggedCard || !grid.contains(target)) return;

    const box = target.getBoundingClientRect();
    const withinTargetRow = event.clientY >= box.top && event.clientY <= box.bottom;
    const before = withinTargetRow
      ? event.clientX < box.left + box.width / 2
      : event.clientY < box.top + box.height / 2;
    const alreadyThere = before
      ? target.previousElementSibling === draggedCard
      : target.nextElementSibling === draggedCard;
    if (alreadyThere) return;

    const positions = capturePositions();
    cards().forEach((card) => card.classList.remove("collection-drop-target"));
    target.classList.add("collection-drop-target");
    if (before) target.before(draggedCard);
    else target.after(draggedCard);
    updateControls();
    animatePositions(positions);
  });

  grid.addEventListener("pointerup", (event) => {
    if (event.pointerId === dragPointerId) finishPointerDrag(event.pointerId);
  });
  grid.addEventListener("pointercancel", (event) => {
    if (event.pointerId === dragPointerId) finishPointerDrag(event.pointerId);
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
      window.setTimeout(() => setReordering(false), 450);
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
