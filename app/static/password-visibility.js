(() => {
  const ZOOM_STORAGE_KEY = "infomancer-ui-zoom";
  const SIDEBAR_STORAGE_KEY = "infomancer-sidebar-width";
  const ZOOM_MIN = 80;
  const ZOOM_MAX = 160;
  const ZOOM_STEP = 10;
  const SIDEBAR_MIN = 220;
  const SIDEBAR_MAX = 380;
  const SIDEBAR_VIEWPORT_RATIO = .32;
  let zoomStatusTimer = 0;
  let sidebarWasResizing = false;

  const storageGet = (key) => {
    try { return window.localStorage.getItem(key); }
    catch (_error) { return null; }
  };

  const storageSet = (key, value) => {
    try { window.localStorage.setItem(key, value); }
    catch (_error) {}
  };

  const clampZoom = (value) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value / ZOOM_STEP) * ZOOM_STEP));
  const savedZoom = Number.parseInt(storageGet(ZOOM_STORAGE_KEY) || "100", 10);
  let currentZoom = clampZoom(Number.isFinite(savedZoom) ? savedZoom : 100);

  const showZoomStatus = () => {
    const status = document.getElementById("ui-zoom-status");
    if (!status) return;
    status.textContent = `Zoom ${currentZoom}%`;
    status.hidden = false;
    window.clearTimeout(zoomStatusTimer);
    zoomStatusTimer = window.setTimeout(() => { status.hidden = true; }, 1200);
  };

  const clampSidebarWidth = ({save = false, preferStored = false} = {}) => {
    if (!document.body?.classList.contains("has-app-sidebar")) return;
    const style = window.getComputedStyle(document.documentElement);
    const rendered = Number.parseFloat(style.getPropertyValue("--app-sidebar-width"));
    const stored = Number.parseInt(storageGet(SIDEBAR_STORAGE_KEY) || "258", 10);
    const requested = preferStored && Number.isFinite(stored)
      ? stored
      : Number.isFinite(rendered) ? rendered : stored;
    const zoomFactor = currentZoom / 100;
    const effectiveViewportWidth = window.innerWidth / zoomFactor;
    const viewportMax = Math.floor(effectiveViewportWidth * SIDEBAR_VIEWPORT_RATIO);
    const maximum = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, viewportMax));
    const next = Math.min(maximum, Math.max(SIDEBAR_MIN, Math.round(requested)));
    document.documentElement.style.setProperty("--app-sidebar-width", `${next}px`);
    if (save) storageSet(SIDEBAR_STORAGE_KEY, String(next));
  };

  const applyZoom = (value, {save = true, announce = true} = {}) => {
    currentZoom = clampZoom(value);
    document.documentElement.style.zoom = String(currentZoom / 100);
    document.documentElement.dataset.uiZoom = String(currentZoom);
    if (save) storageSet(ZOOM_STORAGE_KEY, String(currentZoom));
    clampSidebarWidth({preferStored: true});
    if (announce) showZoomStatus();
  };

  applyZoom(currentZoom, {save: false, announce: false});
  clampSidebarWidth({preferStored: true});

  document.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key;
    const zoomIn = key === "+" || key === "=" || key === "Add";
    const zoomOut = key === "-" || key === "_" || key === "Subtract";
    const zoomReset = key === "0";
    if (!zoomIn && !zoomOut && !zoomReset) return;

    event.preventDefault();
    if (zoomReset) applyZoom(100);
    else applyZoom(currentZoom + (zoomIn ? ZOOM_STEP : -ZOOM_STEP));
  });

  window.addEventListener("resize", () => clampSidebarWidth({preferStored: true}));
  document.addEventListener("pointermove", () => {
    if (!document.body?.classList.contains("sidebar-resizing")) return;
    sidebarWasResizing = true;
    clampSidebarWidth();
  }, {passive: true});
  document.addEventListener("pointerup", () => {
    if (!sidebarWasResizing) return;
    sidebarWasResizing = false;
    window.setTimeout(() => clampSidebarWidth({save: true}), 0);
  }, {passive: true});

  document.addEventListener("DOMContentLoaded", () => {
    const zoomStatus = document.createElement("div");
    zoomStatus.id = "ui-zoom-status";
    zoomStatus.className = "ui-zoom-status";
    zoomStatus.setAttribute("role", "status");
    zoomStatus.setAttribute("aria-live", "polite");
    zoomStatus.hidden = true;
    document.body.append(zoomStatus);

    document.querySelectorAll("[data-password-toggle]").forEach((button) => {
      const input = document.getElementById(button.dataset.passwordToggle);
      if (!input) {
        button.disabled = true;
        button.textContent = "Unavailable";
        button.setAttribute("aria-label", "Password visibility control is unavailable");
        return;
      }

      button.addEventListener("click", () => {
        const willShow = input.type === "password";
        input.type = willShow ? "text" : "password";
        button.textContent = willShow ? "Hide" : "Show";
        button.setAttribute("aria-pressed", String(willShow));
        const description = button.getAttribute("aria-label")
          .replace(/^Show /, "")
          .replace(/^Hide /, "");
        button.setAttribute("aria-label", `${willShow ? "Hide" : "Show"} ${description}`);
        input.focus({ preventScroll: true });
      });
    });
  });
})();
