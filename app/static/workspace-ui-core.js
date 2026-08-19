(() => {
  const Workspace = window.InfoMancerWorkspace = window.InfoMancerWorkspace || {};
  const activeActionControllers = new Set();

  const sameOriginUrl = (value) => {
    try {
      const url = new URL(value, window.location.origin);
      return url.origin === window.location.origin ? url : null;
    } catch (_error) {
      return null;
    }
  };

  const ensureToastHost = () => {
    let host = document.getElementById("workspace-toast-host");
    if (host) return host;
    host = document.createElement("div");
    host.id = "workspace-toast-host";
    host.className = "workspace-toast-host";
    host.setAttribute("aria-live", "polite");
    host.setAttribute("aria-atomic", "false");
    document.body.append(host);
    return host;
  };

  Workspace.toast = (message, type = "success", timeout = 4200) => {
    if (!message) return;
    const host = ensureToastHost();
    const toast = document.createElement("div");
    toast.className = `workspace-toast ${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    const copy = document.createElement("span");
    copy.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notification");
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());
    toast.append(copy, close);
    host.append(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    if (timeout > 0) window.setTimeout(() => {
      toast.classList.remove("visible");
      window.setTimeout(() => toast.remove(), 180);
    }, timeout);
  };

  const ensureConfirmDialog = () => {
    let dialog = document.getElementById("workspace-confirm-dialog");
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "workspace-confirm-dialog";
    dialog.className = "workspace-confirm-dialog";
    dialog.setAttribute("aria-labelledby", "workspace-confirm-title");
    dialog.setAttribute("aria-describedby", "workspace-confirm-copy");
    dialog.innerHTML = `
      <form method="dialog" class="workspace-dialog-card">
        <p class="eyebrow">CONFIRM ACTION</p>
        <h2 id="workspace-confirm-title">Continue?</h2>
        <p id="workspace-confirm-copy" data-workspace-confirm-copy></p>
        <div class="workspace-dialog-actions">
          <button class="button" value="cancel" data-workspace-confirm-cancel>Cancel</button>
          <button class="button primary" value="confirm">Continue</button>
        </div>
      </form>`;
    document.body.append(dialog);
    return dialog;
  };

  Workspace.confirm = (message) => new Promise((resolve) => {
    const dialog = ensureConfirmDialog();
    if (typeof dialog.showModal !== "function") {
      resolve(window.confirm(message));
      return;
    }
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.querySelector("[data-workspace-confirm-copy]").textContent = message;
    dialog.returnValue = "";
    const finish = () => {
      dialog.removeEventListener("close", finish);
      if (opener?.isConnected) opener.focus({preventScroll: true});
      resolve(dialog.returnValue === "confirm");
    };
    dialog.addEventListener("close", finish);
    dialog.showModal();
    requestAnimationFrame(() => dialog.querySelector("[data-workspace-confirm-cancel]")?.focus({preventScroll: true}));
  });

  const ensureDrawer = () => {
    let drawer = document.getElementById("workspace-drawer");
    if (drawer) return drawer;
    drawer = document.createElement("aside");
    drawer.id = "workspace-drawer";
    drawer.className = "workspace-drawer";
    drawer.hidden = true;
    drawer.setAttribute("aria-label", "Workspace details");
    drawer.innerHTML = `
      <button class="workspace-drawer-scrim" type="button" data-workspace-drawer-close aria-label="Close details"></button>
      <section class="workspace-drawer-panel" tabindex="-1">
        <header class="workspace-drawer-chrome"><strong>Details</strong><button type="button" data-workspace-drawer-close aria-label="Close details">×</button></header>
        <div class="workspace-drawer-body" aria-live="polite"></div>
      </section>`;
    document.body.append(drawer);
    return drawer;
  };

  const drawerState = {key: "", param: "drawer", url: "", controller: null, opener: null};

  const setDrawerUrl = (key, param, mode = "push") => {
    const url = new URL(window.location.href);
    if (key) url.searchParams.set(param, key);
    else url.searchParams.delete(param);
    const state = {...(history.state || {}), workspaceDrawerKey: key || null, workspaceDrawerParam: param};
    history[mode === "replace" ? "replaceState" : "pushState"](state, "", url.pathname + url.search + url.hash);
  };

  const closeDrawer = ({historyMode = "replace", restoreFocus = true} = {}) => {
    const drawer = ensureDrawer();
    drawerState.controller?.abort();
    drawerState.controller = null;
    drawer.classList.remove("open");
    document.body.classList.remove("workspace-drawer-open");
    const key = drawerState.key;
    const param = drawerState.param;
    const opener = drawerState.opener;
    drawerState.key = "";
    drawerState.url = "";
    drawerState.opener = null;
    document.querySelectorAll('[data-workspace-drawer-url][aria-expanded="true"]').forEach(node => node.setAttribute("aria-expanded", "false"));
    window.setTimeout(() => { if (!drawerState.key) drawer.hidden = true; }, 180);
    if (historyMode === "back" && history.state?.workspaceDrawerKey === key) history.back();
    else if (historyMode === "replace") setDrawerUrl("", param, "replace");
    if (restoreFocus && opener?.isConnected) requestAnimationFrame(() => opener.focus({preventScroll: true}));
  };

  const enhanceDrawerBody = () => {
    const drawer = ensureDrawer();
    drawer.querySelectorAll("[data-workspace-drawer-url]").forEach((button) => {
      if (button.dataset.workspaceDrawerBound === "1") return;
      button.dataset.workspaceDrawerBound = "1";
      button.addEventListener("click", () => openDrawer(button));
    });
  };

  const openDrawer = async (trigger, historyMode = null) => {
    const rawUrl = trigger?.dataset?.workspaceDrawerUrl || trigger?.url;
    const key = trigger?.dataset?.workspaceDrawerKey || trigger?.key || "";
    const param = trigger?.dataset?.workspaceDrawerParam || trigger?.param || "drawer";
    const url = sameOriginUrl(rawUrl);
    if (!url || !key) return;
    const drawer = ensureDrawer();
    const body = drawer.querySelector(".workspace-drawer-body");
    const panel = drawer.querySelector(".workspace-drawer-panel");
    drawerState.controller?.abort();
    const controller = new AbortController();
    drawerState.controller = controller;
    const replacing = Boolean(drawerState.key);
    if (trigger instanceof HTMLElement) {
      drawerState.opener = trigger;
      trigger.setAttribute("aria-expanded", "true");
    }
    drawerState.key = key;
    drawerState.param = param;
    drawerState.url = url.pathname + url.search;
    drawer.hidden = false;
    body.setAttribute("aria-busy", "true");
    body.innerHTML = '<div class="workspace-drawer-state loading"><span></span><p>Loading details…</p></div>';
    requestAnimationFrame(() => {
      drawer.classList.add("open");
      document.body.classList.add("workspace-drawer-open");
      if (trigger instanceof HTMLElement) panel?.focus({preventScroll: true});
    });
    if (historyMode !== "none") setDrawerUrl(key, param, historyMode || (replacing ? "replace" : "push"));
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
        headers: {"X-Workspace-Drawer": "1"},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const html = await response.text();
      if (drawerState.controller !== controller || controller.signal.aborted) return;
      body.innerHTML = html;
      enhanceDrawerBody();
    } catch (error) {
      if (error.name !== "AbortError" && drawerState.controller === controller) {
        body.innerHTML = '<div class="workspace-drawer-state error"><p>Details could not be loaded. Open the full page or try again.</p></div>';
      }
    } finally {
      if (drawerState.controller === controller) {
        drawerState.controller = null;
        body.removeAttribute("aria-busy");
      }
    }
  };
  Workspace.openDrawer = openDrawer;
  Workspace.closeDrawer = closeDrawer;

  const updateReviewCounts = (counts) => {
    if (!counts) return;
    ["total", "critical", "warning", "information"].forEach((key) => {
      const node = document.querySelector(`[data-review-count="${key}"]`);
      if (node && counts[key] !== undefined) node.textContent = counts[key];
    });
    Object.entries(counts.buckets || {}).forEach(([key, value]) => {
      const node = document.querySelector(`[data-review-bucket-count="${CSS.escape(key)}"]`);
      if (node) node.textContent = value;
    });
  };

  const removeReviewItem = (key) => {
    if (!key) return;
    document.querySelector(`[data-review-item][data-review-key="${CSS.escape(key)}"]`)?.remove();
    const list = document.querySelector("[data-review-list]");
    if (list && !list.querySelector("[data-review-item]")) {
      const empty = list.querySelector("[data-review-empty]");
      if (empty) empty.hidden = false;
    }
  };

  const formDataFor = (form, submitter) => {
    try {
      return submitter ? new FormData(form, submitter) : new FormData(form);
    } catch (_error) {
      const data = new FormData(form);
      if (submitter?.name) data.append(submitter.name, submitter.value || "");
      return data;
    }
  };

  const submitWorkspaceForm = async (form, submitter = null) => {
    if (form.dataset.workspaceSubmitting === "1") return;
    const confirmMessage = form.dataset.workspaceConfirm;
    if (confirmMessage && !(await Workspace.confirm(confirmMessage))) return;
    const action = sameOriginUrl(form.action);
    if (!action) {
      form.submit();
      return;
    }

    const method = (form.method || "POST").toUpperCase();
    const submitters = [...form.querySelectorAll('button[type="submit"], input[type="submit"]')];
    const csrfToken = form.querySelector('input[name="csrf_token"]')?.value || "";
    const controller = new AbortController();
    const data = formDataFor(form, submitter);
    const requestUrl = new URL(action.href);
    let body = data;
    if (method === "GET") {
      for (const [name, value] of data.entries()) {
        if (typeof value === "string") requestUrl.searchParams.append(name, value);
      }
      body = undefined;
    }

    form.dataset.workspaceSubmitting = "1";
    submitters.forEach(button => button.disabled = true);
    form.classList.add("workspace-action-busy");
    activeActionControllers.add(controller);
    try {
      const response = await fetch(requestUrl, {
        method,
        credentials: "same-origin",
        cache: "no-store",
        body,
        signal: controller.signal,
        headers: {
          "Accept": "application/json",
          "X-Workspace-Action": "1",
          ...(csrfToken ? {"X-CSRF-Token": csrfToken} : {}),
        },
      });
      let result = null;
      try { result = await response.json(); } catch (_error) {}
      if (!response.ok) throw new Error(result?.detail || result?.message || `HTTP ${response.status}`);
      Workspace.toast(result?.message || "Action completed.", result?.type || "success");
      const removeKey = result?.remove_key || form.dataset.workspaceRemoveKey;
      if (removeKey) {
        removeReviewItem(removeKey);
        if (drawerState.key === removeKey) closeDrawer({historyMode: "replace", restoreFocus: false});
      }
      updateReviewCounts(result?.counts);
      if (result?.reload_drawer && drawerState.url) {
        openDrawer({url: drawerState.url, key: drawerState.key, param: drawerState.param}, "none");
      }
      document.dispatchEvent(new CustomEvent("infomancer:workspace-action", {detail: result || {}}));
    } catch (error) {
      if (error?.name !== "AbortError") Workspace.toast(error.message || "The action could not be completed.", "error", 6500);
    } finally {
      activeActionControllers.delete(controller);
      delete form.dataset.workspaceSubmitting;
      form.classList.remove("workspace-action-busy");
      submitters.forEach(button => button.disabled = false);
    }
  };

  const enhanceAjaxForms = () => {
    document.addEventListener("submit", (event) => {
      const form = event.target.closest("form[data-workspace-ajax]");
      if (!form) return;
      event.preventDefault();
      submitWorkspaceForm(form, event.submitter || null);
    });
    document.addEventListener("submit", async (event) => {
      const form = event.target.closest("form[data-workspace-confirm]:not([data-workspace-ajax])");
      if (!form || form.dataset.workspaceConfirmed === "1") return;
      event.preventDefault();
      if (!(await Workspace.confirm(form.dataset.workspaceConfirm))) return;
      form.dataset.workspaceConfirmed = "1";
      form.requestSubmit(event.submitter || undefined);
    });
  };

  const enhanceDrawers = () => {
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-workspace-drawer-url]");
      if (!trigger) return;
      event.preventDefault();
      openDrawer(trigger);
    });
    document.addEventListener("click", (event) => {
      if (event.target.closest("[data-workspace-drawer-close]")) closeDrawer({historyMode: history.state?.workspaceDrawerKey ? "back" : "replace"});
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drawerState.key) {
        event.preventDefault();
        closeDrawer({historyMode: history.state?.workspaceDrawerKey ? "back" : "replace"});
      }
    });
    window.addEventListener("popstate", () => {
      const param = history.state?.workspaceDrawerParam || drawerState.param || "review";
      const key = new URL(window.location.href).searchParams.get(param);
      if (!key) {
        if (drawerState.key) closeDrawer({historyMode: "none", restoreFocus: false});
        return;
      }
      const trigger = document.querySelector(`[data-workspace-drawer-key="${CSS.escape(key)}"]`);
      if (trigger) openDrawer(trigger, "none");
    });
    const params = new URL(window.location.href).searchParams;
    for (const param of ["review", "drawer"]) {
      const key = params.get(param);
      if (!key) continue;
      const trigger = document.querySelector(`[data-workspace-drawer-key="${CSS.escape(key)}"]`);
      if (trigger) openDrawer(trigger, "none");
      break;
    }
  };

  const closeMenus = (except = null, {restoreFocus = false} = {}) => {
    document.querySelectorAll("[data-workspace-menu-root].open").forEach((root) => {
      if (root === except) return;
      const toggle = root.querySelector("[data-workspace-menu-toggle]");
      root.classList.remove("open");
      root.querySelector("[data-workspace-menu]")?.setAttribute("hidden", "");
      toggle?.setAttribute("aria-expanded", "false");
      if (restoreFocus) toggle?.focus({preventScroll: true});
    });
  };

  const closeNativePopovers = () => {
    document.querySelectorAll('.item-action-menu[open], .catalog-saved-views[open], .letter-jump-menu[open], .library-bulk-match-menu[open]').forEach(node => node.removeAttribute("open"));
  };

  const enhanceContextMenus = () => {
    document.addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-workspace-menu-toggle]");
      if (!toggle) {
        if (!event.target.closest("[data-workspace-menu]")) closeMenus();
        return;
      }
      event.stopPropagation();
      const root = toggle.closest("[data-workspace-menu-root]");
      const menu = root?.querySelector("[data-workspace-menu]");
      if (!root || !menu) return;
      const opening = !root.classList.contains("open");
      closeMenus(root);
      root.classList.toggle("open", opening);
      menu.hidden = !opening;
      toggle.setAttribute("aria-expanded", String(opening));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeMenus(null, {restoreFocus: true});
        closeNativePopovers();
      }
    });
  };

  const ensureCommandPalette = () => {
    let dialog = document.getElementById("workspace-command-palette");
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "workspace-command-palette";
    dialog.className = "workspace-command-palette";
    dialog.setAttribute("aria-label", "Command palette");
    dialog.innerHTML = `
      <section class="workspace-command-card">
        <header><span>Command palette</span><kbd>Esc</kbd></header>
        <input type="search" data-workspace-command-input placeholder="Type a command or search the library" autocomplete="off" aria-controls="workspace-command-results">
        <div id="workspace-command-results" class="workspace-command-results" data-workspace-command-results role="listbox"></div>
        <footer><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>⌘/Ctrl K</kbd> toggle</span></footer>
      </section>`;
    document.body.append(dialog);
    return dialog;
  };

  const commandEntries = () => {
    const seen = new Set();
    const entries = [];
    document.querySelectorAll("[data-workspace-nav] a[href], [data-workspace-command]").forEach((node) => {
      const href = node.getAttribute("href");
      const label = node.dataset.workspaceCommand || node.textContent.trim().replace(/\s+/g, " ");
      if (!label) return;
      const key = `${label}|${href || ""}`;
      if (seen.has(key)) return;
      seen.add(key);
      entries.push({label, href, node});
    });
    return entries;
  };

  const enhanceCommandPalette = () => {
    const dialog = ensureCommandPalette();
    const input = dialog.querySelector("[data-workspace-command-input]");
    const results = dialog.querySelector("[data-workspace-command-results]");
    let activeIndex = 0;
    let visible = [];
    let baseEntries = [];
    let opener = null;

    const syncActive = () => {
      results.querySelectorAll('button[role="option"]').forEach((candidate, candidateIndex) => {
        const active = candidateIndex === activeIndex;
        candidate.classList.toggle("active", active);
        candidate.setAttribute("aria-selected", String(active));
        if (active) candidate.scrollIntoView({block: "nearest"});
      });
    };

    const render = () => {
      const query = input.value.trim();
      const normalized = query.toLowerCase();
      visible = baseEntries.filter(entry => !normalized || entry.label.toLowerCase().includes(normalized)).slice(0, 12);
      if (query) visible.push({label: `Search library for “${query}”`, href: `/library?q=${encodeURIComponent(query)}&record_search=1`, search: true});
      activeIndex = Math.min(activeIndex, Math.max(0, visible.length - 1));
      results.replaceChildren();
      visible.forEach((entry, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", String(index === activeIndex));
        button.classList.toggle("active", index === activeIndex);
        const strong = document.createElement("strong");
        strong.textContent = entry.label;
        const small = document.createElement("small");
        small.textContent = entry.search ? "Library search" : (entry.href || "Current page action");
        button.append(strong, small);
        button.addEventListener("mouseenter", () => {
          activeIndex = index;
          syncActive();
        });
        button.addEventListener("click", () => run(entry));
        results.append(button);
      });
      if (!visible.length) {
        const empty = document.createElement("p");
        empty.className = "workspace-command-empty";
        empty.textContent = "No matching commands.";
        results.append(empty);
      }
    };

    const run = (entry) => {
      dialog.close();
      if (entry.href) window.location.assign(entry.href);
      else entry.node?.click();
    };

    const open = () => {
      if (typeof dialog.showModal !== "function") return;
      if (dialog.open) {
        dialog.close();
        return;
      }
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      input.value = "";
      activeIndex = 0;
      baseEntries = commandEntries();
      render();
      dialog.showModal();
      window.setTimeout(() => input.focus(), 0);
    };
    Workspace.openCommandPalette = open;

    dialog.addEventListener("close", () => {
      if (opener?.isConnected) opener.focus({preventScroll: true});
      opener = null;
    });

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        open();
        return;
      }
      if (!dialog.open) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        activeIndex = Math.max(0, Math.min(visible.length - 1, activeIndex + offset));
        syncActive();
      } else if (event.key === "Enter" && document.activeElement === input && visible[activeIndex]) {
        event.preventDefault();
        run(visible[activeIndex]);
      }
    });
    input.addEventListener("input", () => { activeIndex = 0; render(); });
  };

  const resetTransientState = ({restoreFocus = false} = {}) => {
    activeActionControllers.forEach(controller => controller.abort());
    activeActionControllers.clear();
    closeMenus(null, {restoreFocus});
    closeNativePopovers();
    if (drawerState.key) closeDrawer({historyMode: "none", restoreFocus});
    const palette = document.getElementById("workspace-command-palette");
    if (palette?.open) palette.close();
  };
  Workspace.closeTransientUi = resetTransientState;

  const enhanceLifecycle = () => {
    document.addEventListener("infomancer:before-navigate", () => resetTransientState({restoreFocus: false}));
    window.addEventListener("pageshow", (event) => {
      if (!event.persisted) return;
      resetTransientState({restoreFocus: false});
      document.querySelectorAll(".workspace-action-busy").forEach(form => {
        form.classList.remove("workspace-action-busy");
        delete form.dataset.workspaceSubmitting;
        form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(button => button.disabled = false);
      });
    });
  };

  const init = () => {
    enhanceAjaxForms();
    enhanceDrawers();
    enhanceContextMenus();
    enhanceCommandPalette();
    enhanceLifecycle();
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, {once: true});
  else init();
})();
