(() => {
  const tour = document.getElementById("onboarding-tour");
  if (!tour) return;

  const post = async (url, csrfToken, values = {}) => {
    const body = new URLSearchParams(values);
    const response = await fetch(url, {
      method: "POST",
      headers: {"X-CSRF-Token": csrfToken, "Content-Type": "application/x-www-form-urlencoded"},
      body,
    });
    if (!response.ok) throw new Error("InfoMancer could not save that choice. Refresh the page and try again.");
  };

  const title = document.getElementById("tour-title");
  const copy = document.getElementById("tour-copy");
  const next = document.getElementById("tour-next");
  const back = document.getElementById("tour-back");
  const skip = document.getElementById("tour-skip");
  const label = document.getElementById("tour-step-label");
  const progress = document.getElementById("tour-progress-bar");
  const card = tour.querySelector(".tour-card");
  const scrim = document.getElementById("tour-scrim");
  if (!title || !copy || !next || !back || !skip || !label || !progress || !card || !scrim) return;

  const shades = Object.fromEntries(
    [...scrim.querySelectorAll("[data-tour-shade]")].map((shade) => [shade.dataset.tourShade, shade]),
  );
  const isLibrarian = document.body.classList.contains("role-librarian");
  const mobileTour = window.matchMedia("(max-width: 760px), (hover: none) and (pointer: coarse)");
  const libraryPaths = new Set(["/library", "/movies", "/shows"]);
  const navigationCopy = isLibrarian
    ? "Dashboard, Library, Review, Sources, Activity, and Settings live in one navigation system. On phones, the menu button opens the same workspace without changing what each destination does."
    : "Dashboard, Library, Review, and Activity live in one navigation system. On phones, the menu button opens the same workspace without changing what each destination does.";

  const steps = [
    {
      id: "welcome",
      path: "/",
      title: "Meet the 0.8 workspace",
      copy: "InfoMancer is built around a simple flow: browse what you have, inspect the evidence, review what needs a decision, then explicitly choose when to make changes. The walkthrough itself never changes your media files.",
    },
    {
      id: "navigation",
      path: "/",
      target: "#site-menu-panel",
      action: "menu",
      title: "One workspace, wherever you are",
      copy: navigationCopy,
    },
    {
      id: "library-scope",
      path: "/library",
      target: ".catalog-tabs",
      group: [".saved-view-bar"],
      title: "Scope it once, save it for later",
      copy: "Switch between All Media, Movies, and TV Shows without learning different tools. Saved Views can remember a useful filter and sort combination, and pinned views stay close in Library and Dashboard.",
    },
    {
      id: "filters",
      path: "/library",
      target: ".library-controls",
      title: "Find the exact slice you need",
      copy: "Library search understands titles, filenames, people, and tags. Filter by genre, type, favorites, or custom tags, then open More Filters for source, match state, episode gaps, and sorting.",
    },
    {
      id: "display",
      path: "/library",
      target: ".library-view-controls",
      libraryView: "covers",
      title: "Choose detail or artwork",
      copy: "List view keeps catalog details close. Cover view turns the same results into a visual shelf. Cover density changes how much fits on screen, and InfoMancer remembers the view you prefer.",
    },
    {
      id: "inspector",
      path: "/library",
      target: ".tour-demo-list article, .library-table tbody tr",
      libraryView: "list",
      title: "Inspect first, act second",
      copy: "Select a title to open Inspector without losing your place. Inspector brings media facts, matching and metadata state, technical details, organization, and quick tools together. Select multiple titles for bulk organization, metadata refresh, or matching tools.",
    },
    {
      id: "review",
      path: "/review",
      target: ".review-summary-strip",
      title: "Review is your decision inbox",
      copy: "Review brings Media Intelligence findings, duplicates, unmatched media, missing episodes, metadata and quality issues, and persisted rename proposals into one decision queue. Open an item to inspect the evidence, decide what to do, and stay in the queue.",
    },
    ...(isLibrarian ? [
      {
        id: "sources",
        path: "/sources",
        target: ".source-add-panel",
        title: "Sources stay explicit and guarded",
        copy: "Add Movie and TV folders through the browser or a trusted path, check connections, and scan them independently. Source Guard preserves catalog records when storage is offline or a scan fails, and removing a Source never deletes its media files.",
      },
      {
        id: "safety",
        path: "/settings/system",
        target: ".system-safety-card",
        title: "Choose how much file authority to allow",
        copy: "Read-Only Mode blocks media-file changes while catalog and analysis tools keep working. Standard Mode allows reviewed changes. Lockdown Mode keeps reversible work available but adds stronger protection around irreversible deletion and pauses automatic permanent Managed Trash cleanup.",
      },
      {
        id: "scheduled-tasks",
        path: "/settings/scheduled-tasks",
        target: ".scheduled-task-layout",
        title: "Schedule maintenance without babysitting it",
        copy: "Scheduled Tasks controls background fingerprinting and Managed Trash retention. Fingerprinting can run automatically, on a schedule, or on demand, while protection modes can pause permanent cleanup. Once scheduled work starts, live progress moves into the task widget.",
      },
      {
        id: "recovery",
        path: "/settings/recovery",
        target: "#recovery-upload-form",
        title: "Recovery is preview-first too",
        copy: "Portable .infomancer-backup packages are verified before restore, then InfoMancer shows what will be restored before touching the live installation. Recovery creates a safety package first and restores catalog data and collection artwork, never your media files, provider credentials, or deployment secrets.",
      },
      {
        id: "operations",
        path: "/operations",
        target: ".operation-history-summary",
        title: "Every supported file change leaves a trail",
        copy: "Operation History records supported renames, season-folder moves, and Managed Trash work. Safe Undo revalidates the current catalog, paths, source boundaries, and collision state before reversing an eligible action, and refuses to change anything when the state has drifted.",
      },
    ] : []),
    {
      id: "tasks",
      path: "/review",
      target: ".task-widget",
      action: "task-demo",
      title: "Background work stays out of your way",
      copy: "Scans, matching, metadata work, and other long jobs report progress here while you keep using InfoMancer. Open the task panel for details. Supported scan and fingerprint jobs can also be cancelled safely from there.",
    },
    {
      id: "global-search",
      path: "/review",
      target: ".global-search-toggle",
      title: "Search and commands follow you",
      copy: "The header search can jump to a title from anywhere and recent searches help you revisit earlier lookups. On desktop, Ctrl/Cmd+K opens the command palette for fast navigation and Library search without leaving the keyboard.",
    },
    {
      id: "profile",
      path: "/review",
      target: ".account-menu > summary",
      action: "profile",
      title: "Your account and preferences",
      copy: "Your profile holds display and reading preferences, account security, active sessions, Help, and the option to replay this walkthrough later. Nothing in this tour changes your media files.",
    },
  ];

  const requestedStep = Number.parseInt(new URLSearchParams(window.location.search).get("tour_step") || "0", 10);
  let index = Number.isInteger(requestedStep) ? Math.min(Math.max(requestedStep, 0), steps.length - 1) : 0;
  let highlighted = null;
  let highlightedGroup = [];
  let taskDemoTimer = 0;
  let taskDemoSnapshot = null;
  let menuSnapshot = null;
  let profileWasOpen = false;
  let layoutFrame = 0;
  let pendingLibraryView = "";
  const tourViewStorageKey = "infomancer-tour-original-library-view";

  const currentStep = () => steps[index];
  const isLibraryPath = (path) => libraryPaths.has(path);
  const libraryControllerReady = () => Boolean(
    document.querySelector('script[src*="library-surface-lazy.js"][data-infomancer-loaded="1"]'),
  );
  const libraryViewButton = (view) => document.getElementById(
    view === "covers" ? "library-cover-view" : "library-list-view",
  );
  const applyTourLibraryView = (view) => {
    if (!view) return;
    pendingLibraryView = "";
    libraryViewButton(view)?.click();
    document.querySelector(".tour-library-demo")?.setAttribute("data-view", view);
  };

  /* The Library controller is loaded by app-shell.js after the base tour script.
     Queue the requested demo view until that controller announces its first view,
     rather than making the tour's behavior depend on device or network speed. */
  document.addEventListener("infomancer:library-view-changed", () => {
    if (!pendingLibraryView) return;
    const requested = pendingLibraryView;
    requestAnimationFrame(() => applyTourLibraryView(requested));
  });

  const restoreLibraryView = () => {
    const saved = sessionStorage.getItem(tourViewStorageKey);
    if (saved === null) return;
    try {
      const original = JSON.parse(saved);
      pendingLibraryView = "";
      if (libraryControllerReady()) libraryViewButton(original.view)?.click();
      if (original.persisted === null) localStorage.removeItem("infomancer-library-view");
      else localStorage.setItem("infomancer-library-view", original.persisted);
    } catch (_error) {
      // A corrupt session-only tour value should never block the walkthrough.
    }
    sessionStorage.removeItem(tourViewStorageKey);
  };

  const showLibraryView = (view) => {
    if (!view) return;
    if (sessionStorage.getItem(tourViewStorageKey) === null) {
      sessionStorage.setItem(tourViewStorageKey, JSON.stringify({
        persisted: localStorage.getItem("infomancer-library-view"),
        view: document.getElementById("library-cover-view")?.getAttribute("aria-pressed") === "true" ? "covers" : "list",
      }));
    }
    document.querySelector(".tour-library-demo")?.setAttribute("data-view", view);
    if (libraryControllerReady()) {
      applyTourLibraryView(view);
      return;
    }
    pendingLibraryView = view;
  };

  const goToStep = (stepIndex, replace = false) => {
    const destination = steps[stepIndex];
    if (isLibraryPath(window.location.pathname) && !isLibraryPath(destination.path)) restoreLibraryView();
    const url = new URL(destination.path, window.location.origin);
    url.searchParams.set("tour", "1");
    url.searchParams.set("tour_step", String(stepIndex));
    window.location[replace ? "replace" : "assign"](url.href);
  };

  if (window.location.pathname !== currentStep().path) {
    goToStep(index, true);
    return;
  }

  const setShade = (shade, styles) => {
    if (shade) Object.assign(shade.style, styles);
  };

  const highlightedElements = () => [highlighted, ...highlightedGroup].filter(Boolean);

  const highlightedRect = () => {
    const elements = highlightedElements();
    if (!elements.length) return null;
    const rects = elements.map((element) => element.getBoundingClientRect());
    return {
      left: Math.min(...rects.map((rect) => rect.left)),
      top: Math.min(...rects.map((rect) => rect.top)),
      right: Math.max(...rects.map((rect) => rect.right)),
      bottom: Math.max(...rects.map((rect) => rect.bottom)),
      width: Math.max(...rects.map((rect) => rect.right)) - Math.min(...rects.map((rect) => rect.left)),
      height: Math.max(...rects.map((rect) => rect.bottom)) - Math.min(...rects.map((rect) => rect.top)),
    };
  };

  const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), maximum);

  const positionTourCard = () => {
    if (index === 0 || !highlighted) {
      tour.classList.add("tour-intro");
      ["left", "top", "right", "bottom", "transform"].forEach((property) => card.style.removeProperty(property));
      return;
    }

    tour.classList.remove("tour-intro");
    if (mobileTour.matches) {
      ["left", "top", "right", "bottom", "transform"].forEach((property) => card.style.removeProperty(property));
      return;
    }

    const target = highlightedRect();
    if (!target) return;
    const width = card.offsetWidth;
    const height = card.offsetHeight;
    const margin = 18;
    const gap = 20;
    const maxLeft = Math.max(margin, window.innerWidth - width - margin);
    const maxTop = Math.max(margin, window.innerHeight - height - margin);
    const place = (left, top, preference) => ({
      left: clamp(left, margin, maxLeft),
      top: clamp(top, margin, maxTop),
      preference,
    });
    const candidates = [
      place(target.left, target.bottom + gap, 0),
      place(target.right - width, target.bottom + gap, 1),
      place(target.left, target.top - height - gap, 2),
      place(target.right + gap, target.top, 3),
      place(target.left - width - gap, target.top, 4),
      place(maxLeft, maxTop, 5),
      place(margin, maxTop, 6),
      place(maxLeft, margin, 7),
      place(margin, margin, 8),
    ];
    const paddedTarget = {
      left: target.left - 16,
      top: target.top - 16,
      right: target.right + 16,
      bottom: target.bottom + 16,
    };
    const overlap = (candidate) => {
      const right = candidate.left + width;
      const bottom = candidate.top + height;
      return Math.max(0, Math.min(right, paddedTarget.right) - Math.max(candidate.left, paddedTarget.left))
        * Math.max(0, Math.min(bottom, paddedTarget.bottom) - Math.max(candidate.top, paddedTarget.top));
    };
    const targetCenterX = (target.left + target.right) / 2;
    const targetCenterY = (target.top + target.bottom) / 2;
    const score = (candidate) => {
      const centerX = candidate.left + width / 2;
      const centerY = candidate.top + height / 2;
      const distance = Math.hypot(centerX - targetCenterX, centerY - targetCenterY);
      return overlap(candidate) * 1000 + distance + candidate.preference * 4;
    };
    const position = candidates.reduce((best, candidate) => score(candidate) < score(best) ? candidate : best);
    Object.assign(card.style, {
      left: `${position.left}px`,
      top: `${position.top}px`,
      right: "auto",
      bottom: "auto",
      transform: "none",
    });
  };

  const updateSpotlight = () => {
    const rect = highlightedRect();
    if (!rect) {
      scrim.classList.remove("has-spotlight");
      setShade(shades.top, {inset: "0", width: "auto", height: "auto"});
      [shades.right, shades.bottom, shades.left].forEach((shade) => setShade(shade, {inset: "auto", width: "0", height: "0"}));
      return;
    }

    const gap = mobileTour.matches ? 14 : 22;
    const top = Math.max(0, rect.top - gap);
    const right = Math.min(window.innerWidth, rect.right + gap);
    const bottom = Math.min(window.innerHeight, rect.bottom + gap);
    const left = Math.max(0, rect.left - gap);
    scrim.classList.add("has-spotlight");
    setShade(shades.top, {inset: "0 0 auto 0", width: "auto", height: `${top}px`});
    setShade(shades.right, {inset: `${top}px 0 auto auto`, width: `${Math.max(0, window.innerWidth - right)}px`, height: `${Math.max(0, bottom - top)}px`});
    setShade(shades.bottom, {inset: `${bottom}px 0 0 0`, width: "auto", height: "auto"});
    setShade(shades.left, {inset: `${top}px auto auto 0`, width: `${left}px`, height: `${Math.max(0, bottom - top)}px`});
  };

  const ensureTargetVisible = () => {
    if (!highlighted || index === 0) return;
    const rect = highlightedRect();
    if (!rect) return;
    const headerAllowance = 84;
    const cardAllowance = mobileTour.matches ? Math.min(card.offsetHeight + 26, window.innerHeight * 0.48) : 36;
    const availableBottom = window.innerHeight - cardAllowance;
    if (rect.top >= headerAllowance && rect.bottom <= availableBottom) return;

    const targetCenter = (rect.top + rect.bottom) / 2;
    const visibleCenter = headerAllowance + Math.max(40, (availableBottom - headerAllowance) / 2);
    window.scrollBy({top: targetCenter - visibleCenter, behavior: "auto"});
  };

  const updateTourLayout = () => {
    window.cancelAnimationFrame(layoutFrame);
    layoutFrame = window.requestAnimationFrame(() => {
      layoutFrame = 0;
      updateSpotlight();
      positionTourCard();
    });
  };

  const stopTaskDemo = () => {
    window.clearInterval(taskDemoTimer);
    taskDemoTimer = 0;
    const widget = document.getElementById("task-widget");
    if (!widget || widget.dataset.tourDemo !== "1") return;
    delete widget.dataset.tourDemo;
    widget.classList.remove("tour-task-demo");
    widget.querySelector(".tour-task-demo-progress")?.remove();
    if (taskDemoSnapshot) {
      const summary = document.getElementById("task-summary");
      const detail = document.getElementById("task-card-detail");
      if (summary) summary.textContent = taskDemoSnapshot.summary;
      if (detail) detail.textContent = taskDemoSnapshot.detail;
      widget.classList.toggle("idle", taskDemoSnapshot.idle);
      widget.classList.toggle("visible", taskDemoSnapshot.visible);
    }
    taskDemoSnapshot = null;
  };

  const startTaskDemo = () => {
    const widget = document.getElementById("task-widget");
    const toggle = document.getElementById("task-widget-toggle");
    const summary = document.getElementById("task-summary");
    const detail = document.getElementById("task-card-detail");
    if (!widget || !toggle || !summary || !detail || !widget.classList.contains("idle")) return;
    taskDemoSnapshot = {
      summary: summary.textContent,
      detail: detail.textContent,
      idle: widget.classList.contains("idle"),
      visible: widget.classList.contains("visible"),
    };
    widget.dataset.tourDemo = "1";
    widget.classList.remove("idle");
    widget.classList.add("visible", "tour-task-demo");
    summary.textContent = "Scanning Movie Library";
    let checked = 1284;
    const total = 4820;
    detail.textContent = `${checked.toLocaleString()} of ${total.toLocaleString()} files checked`;
    const track = document.createElement("span");
    track.className = "tour-task-demo-progress";
    track.setAttribute("aria-hidden", "true");
    track.innerHTML = "<i></i>";
    toggle.append(track);
    taskDemoTimer = window.setInterval(() => {
      checked = Math.min(total, checked + 137);
      detail.textContent = `${checked.toLocaleString()} of ${total.toLocaleString()} files checked`;
      track.style.setProperty("--tour-task-progress", `${(checked / total) * 100}%`);
      if (checked === total) checked = 1284;
    }, 900);
  };

  const openMenuForTour = () => {
    if (!mobileTour.matches) return;
    const siteMenu = document.getElementById("site-menu");
    const toggle = document.getElementById("site-menu-toggle");
    const panel = document.getElementById("site-menu-panel");
    if (!siteMenu || !toggle || !panel) return;
    menuSnapshot = {
      expanded: toggle.getAttribute("aria-expanded"),
      hidden: panel.getAttribute("aria-hidden"),
    };
    siteMenu.classList.add("tour-menu-open");
    toggle.setAttribute("aria-expanded", "true");
    panel.setAttribute("aria-hidden", "false");
  };

  const closeMenuForTour = () => {
    const siteMenu = document.getElementById("site-menu");
    const toggle = document.getElementById("site-menu-toggle");
    const panel = document.getElementById("site-menu-panel");
    siteMenu?.classList.remove("tour-menu-open");
    if (menuSnapshot && toggle && panel) {
      toggle.setAttribute("aria-expanded", menuSnapshot.expanded ?? "false");
      panel.setAttribute("aria-hidden", menuSnapshot.hidden ?? "true");
    }
    menuSnapshot = null;
  };

  const openProfileForTour = () => {
    const account = document.querySelector(".account-menu");
    if (!account) return;
    profileWasOpen = account.hasAttribute("open");
    account.setAttribute("open", "");
  };

  const closeProfileForTour = () => {
    const account = document.querySelector(".account-menu");
    if (account && !profileWasOpen) account.removeAttribute("open");
    profileWasOpen = false;
  };

  const clearHighlight = () => {
    stopTaskDemo();
    closeMenuForTour();
    closeProfileForTour();
    highlighted?.classList.remove("tour-highlight");
    highlightedGroup.forEach((element) => element.classList.remove("tour-highlight"));
    highlighted = null;
    highlightedGroup = [];
  };

  const resolveTargets = (step) => {
    highlighted = step.target ? document.querySelector(step.target) : null;
    highlightedGroup = (step.group || [])
      .map((selector) => document.querySelector(selector))
      .filter(Boolean)
      .filter((element) => element !== highlighted);
    highlighted?.classList.add("tour-highlight");
    highlightedGroup.forEach((element) => element.classList.add("tour-highlight"));
  };

  const render = () => {
    clearHighlight();
    const step = currentStep();
    showLibraryView(step.libraryView);
    if (step.action === "menu") openMenuForTour();
    if (step.action === "profile") openProfileForTour();
    if (step.action === "task-demo") startTaskDemo();

    resolveTargets(step);
    if (step.action === "menu" && mobileTour.matches) {
      const toggle = document.getElementById("site-menu-toggle");
      const panel = document.getElementById("site-menu-panel");
      highlighted?.classList.remove("tour-highlight");
      highlighted = toggle || panel;
      highlightedGroup = panel && panel !== highlighted ? [panel] : [];
      highlighted?.classList.add("tour-highlight");
      highlightedGroup.forEach((element) => element.classList.add("tour-highlight"));
    }
    if (step.action === "profile") {
      const panel = document.querySelector(".account-menu-popover");
      if (panel && panel !== highlighted) {
        highlightedGroup.push(panel);
        panel.classList.add("tour-highlight");
      }
    }

    title.textContent = step.title;
    copy.textContent = step.copy;
    label.textContent = `${index + 1} of ${steps.length}`;
    progress.style.width = `${((index + 1) / steps.length) * 100}%`;
    next.textContent = index === steps.length - 1 ? "Finish" : index === 0 ? "Start tour" : "Next";
    back.hidden = index === 0;

    const url = new URL(window.location.href);
    url.searchParams.set("tour", "1");
    url.searchParams.set("tour_step", String(index));
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);

    tour.hidden = false;
    card.setAttribute("tabindex", "-1");
    requestAnimationFrame(() => {
      ensureTargetVisible();
      requestAnimationFrame(() => {
        updateTourLayout();
        try { card.focus({preventScroll: true}); }
        catch (_error) { card.focus(); }
      });
    });
    if (step.action === "menu" || step.action === "profile") window.setTimeout(updateTourLayout, 260);
  };

  const close = async (state) => {
    clearHighlight();
    try { await post("/engagement/tour", tour.dataset.csrfToken, {state}); }
    catch (error) {
      window.alert(error.message);
      render();
      return;
    }
    window.removeEventListener("resize", updateTourLayout);
    window.removeEventListener("scroll", updateTourLayout, true);
    window.visualViewport?.removeEventListener("resize", updateTourLayout);
    window.visualViewport?.removeEventListener("scroll", updateTourLayout);
    restoreLibraryView();
    if (tour.dataset.setupPending === "1") {
      window.location.assign("/?setup_prompt=1");
      return;
    }
    tour.remove();
    const url = new URL(window.location.href);
    url.searchParams.delete("tour");
    url.searchParams.delete("tour_step");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
  };

  next.addEventListener("click", () => {
    if (index === steps.length - 1) {
      close("completed");
      return;
    }
    const nextIndex = index + 1;
    if (steps[nextIndex].path !== window.location.pathname) goToStep(nextIndex);
    else {
      index = nextIndex;
      render();
    }
  });

  back.addEventListener("click", () => {
    if (index === 0) return;
    const previousIndex = index - 1;
    if (steps[previousIndex].path !== window.location.pathname) goToStep(previousIndex);
    else {
      index = previousIndex;
      render();
    }
  });

  skip.addEventListener("click", () => close("dismissed"));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !document.body.contains(tour)) return;
    event.preventDefault();
    close("dismissed");
  });
  window.addEventListener("resize", updateTourLayout, {passive: true});
  window.addEventListener("scroll", updateTourLayout, {passive: true, capture: true});
  window.visualViewport?.addEventListener("resize", updateTourLayout, {passive: true});
  window.visualViewport?.addEventListener("scroll", updateTourLayout, {passive: true});
  mobileTour.addEventListener?.("change", () => requestAnimationFrame(() => {
    ensureTargetVisible();
    updateTourLayout();
  }));

  render();
})();