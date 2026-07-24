(() => {
  const post = async (url, csrfToken, values = {}) => {
    const body = new URLSearchParams(values);
    const response = await fetch(url, {
      method: "POST",
      headers: {"X-CSRF-Token": csrfToken, "Content-Type": "application/x-www-form-urlencoded"},
      body,
    });
    if (!response.ok) throw new Error("InfoMancer could not save that choice. Refresh the page and try again.");
  };

  const tour = document.getElementById("onboarding-tour");
  if (tour) {
    const title = document.getElementById("tour-title");
    const copy = document.getElementById("tour-copy");
    const next = document.getElementById("tour-next");
    const back = document.getElementById("tour-back");
    const skip = document.getElementById("tour-skip");
    const label = document.getElementById("tour-step-label");
    const progress = document.getElementById("tour-progress-bar");
    const card = tour.querySelector(".tour-card");
    const scrim = document.getElementById("tour-scrim");
    const shades = Object.fromEntries(
      [...scrim.querySelectorAll("[data-tour-shade]")].map((shade) => [shade.dataset.tourShade, shade]),
    );
    const steps = [
      [null, "Your media library, accounted for.", "This quick walkthrough shows you where to browse, search, follow background work, and manage your account.", "/", null],
      [".library-portals", "Choose the library you need", "Open the complete catalog, browse Movies, or focus on TV Shows. Each destination keeps the same filters and display tools.", "/", null],
      [".tour-demo-list, .library-table", "List view keeps the details close", "List view is designed for catalog work: paths, matching status, episode counts, missing episodes, selections, and title actions.", "/movies", "list"],
      [".tour-demo-covers, #cover-library", "Cover view puts artwork first", "Cover view gives you a visual shelf with the title, rating, and release years. Hover—or tap on mobile—to reveal title actions.", "/movies", "covers"],
      [".library-view-controls", "Make the library yours", "Switch between List and Covers here. In Cover view, use the minus, slider, and plus controls to choose a comfortable poster size.", "/movies", "covers", null],
      [".announcement-heading", "Never miss what changed", "This is the Announcements center, where you can revisit InfoMancer release notes and messages from your installation's Librarians.", "/announcements", null, null],
      [".global-search-toggle", "Search from anywhere", "Use the search button in the header to find a movie, series, or filename without leaving the page you are using.", "/movies", null, null],
      [".task-widget-toggle", "Background work stays visible", "Scans, matching, and metadata updates report their status here while you continue using InfoMancer.", "/movies", null, null],
      [".site-menu-toggle", "Everything is in the main menu", "Open Home, Movies, TV Shows, Announcements, and any Librarian tools from this menu.", "/movies", null, "menu"],
      [".account-menu > summary", "Your profile and account", "Open your profile, change your password, review active sessions, open Help, or replay this tour from the Profile button at the far right.", "/movies", null, "profile"],
    ];
    const requestedStep = Number.parseInt(new URLSearchParams(window.location.search).get("tour_step") || "0", 10);
    let index = Number.isInteger(requestedStep) ? Math.min(Math.max(requestedStep, 0), steps.length - 1) : 0;
    let highlighted = null;
    let highlightedGroup = [];
    const tourViewStorageKey = "infomancer-tour-original-library-view";
    const restoreLibraryView = () => {
      const saved = sessionStorage.getItem(tourViewStorageKey);
      if (saved === null) return;
      const original = JSON.parse(saved);
      document.getElementById(original.view === "covers" ? "library-cover-view" : "library-list-view")?.click();
      if (original.persisted === null) localStorage.removeItem("infomancer-library-view");
      else localStorage.setItem("infomancer-library-view", original.persisted);
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
      document.getElementById(view === "covers" ? "library-cover-view" : "library-list-view")?.click();
      document.querySelector(".tour-library-demo")?.setAttribute("data-view", view);
    };
    const goToStep = (stepIndex, replace = false) => {
      if (window.location.pathname === "/movies" && steps[stepIndex][3] !== "/movies") {
        restoreLibraryView();
      }
      const url = new URL(steps[stepIndex][3], window.location.origin);
      url.searchParams.set("tour", "1");
      url.searchParams.set("tour_step", String(stepIndex));
      window.location[replace ? "replace" : "assign"](url.href);
    };
    if (window.location.pathname !== steps[index][3]) {
      goToStep(index, true);
      return;
    }
    const setShade = (shade, styles) => Object.assign(shade.style, styles);
    const highlightedRect = () => {
      const elements = [highlighted, ...highlightedGroup].filter(Boolean);
      if (!elements.length) return null;
      const rects = elements.map((element) => element.getBoundingClientRect());
      return {
        left: Math.min(...rects.map((rect) => rect.left)),
        top: Math.min(...rects.map((rect) => rect.top)),
        right: Math.max(...rects.map((rect) => rect.right)),
        bottom: Math.max(...rects.map((rect) => rect.bottom)),
      };
    };
    const positionTourCard = () => {
      if (index === 0 || !highlighted) {
        tour.classList.add("tour-intro");
        ["left", "top", "right", "bottom", "transform"].forEach((property) => card.style.removeProperty(property));
        return;
      }

      tour.classList.remove("tour-intro");
      const width = card.offsetWidth;
      const height = card.offsetHeight;
      const margin = Math.min(24, Math.max(8, (window.innerWidth - width) / 2));
      const maxLeft = Math.max(margin, window.innerWidth - width - margin);
      const maxTop = Math.max(margin, window.innerHeight - height - margin);
      const target = highlightedRect();
      const paddedTarget = {
        left: target.left - 18,
        top: target.top - 18,
        right: target.right + 18,
        bottom: target.bottom + 18,
      };
      const candidates = [
        {left: maxLeft, top: margin},
        {left: maxLeft, top: maxTop},
        {left: margin, top: maxTop},
        {left: margin, top: margin},
      ];
      const overlap = (candidate) => {
        const right = candidate.left + width;
        const bottom = candidate.top + height;
        return Math.max(0, Math.min(right, paddedTarget.right) - Math.max(candidate.left, paddedTarget.left))
          * Math.max(0, Math.min(bottom, paddedTarget.bottom) - Math.max(candidate.top, paddedTarget.top));
      };
      const position = candidates.reduce((best, candidate) => overlap(candidate) < overlap(best) ? candidate : best);
      Object.assign(card.style, {
        left: `${position.left}px`,
        top: `${position.top}px`,
        right: "auto",
        bottom: "auto",
        transform: "none",
      });
    };
    const updateSpotlight = () => {
      if (!highlighted) {
        scrim.classList.remove("has-spotlight");
        setShade(shades.top, {inset: "0", width: "auto", height: "auto"});
        [shades.right, shades.bottom, shades.left].forEach((shade) => setShade(shade, {inset: "auto", width: "0", height: "0"}));
        return;
      }

      const gap = 12;
      const rect = highlightedRect();
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
    const updateTourLayout = () => {
      updateSpotlight();
      positionTourCard();
    };
    const clearHighlight = () => {
      highlighted?.classList.remove("tour-highlight");
      highlightedGroup.forEach((element) => element.classList.remove("tour-highlight"));
      highlightedGroup = [];
      highlighted = null;
      document.getElementById("site-menu")?.classList.remove("tour-menu-open");
      document.querySelector(".account-menu")?.removeAttribute("open");
    };
    const render = () => {
      clearHighlight();
      const [selector, heading, text, , libraryView, action] = steps[index];
      showLibraryView(libraryView);
      if (action === "menu") document.getElementById("site-menu")?.classList.add("tour-menu-open");
      if (action === "profile") document.querySelector(".account-menu")?.setAttribute("open", "");
      title.textContent = heading;
      copy.textContent = text;
      label.textContent = `${index + 1} of ${steps.length}`;
      progress.style.width = `${((index + 1) / steps.length) * 100}%`;
      next.textContent = index === steps.length - 1 ? "Finish" : index === 0 ? "Start tour" : "Next";
      back.hidden = index === 0;
      highlighted = selector ? document.querySelector(selector) : null;
      highlighted?.classList.add("tour-highlight");
      if (action === "menu") {
        const menuPanel = document.getElementById("site-menu-panel");
        if (menuPanel) {
          highlightedGroup = [menuPanel];
          menuPanel.classList.add("tour-highlight");
        }
      }
      if (action === "profile") {
        const profilePanel = document.querySelector(".account-menu-popover");
        if (profilePanel) {
          highlightedGroup = [profilePanel];
          profilePanel.classList.add("tour-highlight");
        }
      }
      const url = new URL(window.location.href);
      url.searchParams.set("tour", "1");
      url.searchParams.set("tour_step", String(index));
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
      requestAnimationFrame(updateTourLayout);
      if (action === "menu") window.setTimeout(updateTourLayout, 320);
      if (action === "profile") window.setTimeout(() => {
        document.querySelector(".account-menu")?.setAttribute("open", "");
        updateTourLayout();
      }, 0);
    };
    const close = async (state) => {
      clearHighlight();
      try { await post("/engagement/tour", tour.dataset.csrfToken, {state}); }
      catch (error) { window.alert(error.message); return; }
      window.removeEventListener("resize", updateTourLayout);
      window.removeEventListener("scroll", updateTourLayout, true);
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
      if (index === steps.length - 1) close("completed");
      else {
        const nextIndex = index + 1;
        if (steps[nextIndex][3] !== window.location.pathname) goToStep(nextIndex);
        else { index = nextIndex; render(); }
      }
    });
    back.addEventListener("click", () => {
      if (index === 0) return;
      const previousIndex = index - 1;
      if (steps[previousIndex][3] !== window.location.pathname) goToStep(previousIndex);
      else { index = previousIndex; render(); }
    });
    skip.addEventListener("click", () => close("dismissed"));
    window.addEventListener("resize", updateTourLayout);
    window.addEventListener("scroll", updateTourLayout, true);
    render();
  }

  const popup = document.getElementById("announcement-popup");
  if (popup) {
    const dismiss = document.getElementById("announcement-dismiss");
    const seen = () => post(
      `/engagement/announcements/${popup.dataset.announcementId}/seen`,
      popup.dataset.csrfToken,
    );
    const seenRequest = seen();
    seenRequest.catch(() => {});
    dismiss.addEventListener("click", async () => {
      try { await seenRequest; }
      catch (error) { window.alert(error.message); return; }
      popup.remove();
    });
  }
})();
