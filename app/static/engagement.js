(() => {
  const current = document.currentScript;
  const version = current?.src ? new URL(current.src).search : "";

  const post = async (url, csrfToken, values = {}) => {
    const body = new URLSearchParams(values);
    const response = await fetch(url, {
      method: "POST",
      headers: {"X-CSRF-Token": csrfToken, "Content-Type": "application/x-www-form-urlencoded"},
      body,
    });
    if (!response.ok) throw new Error("InfoMancer could not save that choice. Refresh the page and try again.");
  };

  const loadStyle = (path) => new Promise((resolve) => {
    const href = `/static/${path}${version}`;
    const absolute = new URL(href, window.location.href).href;
    const existing = [...document.querySelectorAll('link[rel="stylesheet"]')]
      .find((link) => link.href === absolute);
    if (existing) {
      if (existing.sheet) resolve(existing);
      else {
        existing.addEventListener("load", () => resolve(existing), {once: true});
        existing.addEventListener("error", () => resolve(existing), {once: true});
      }
      return;
    }
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.addEventListener("load", () => resolve(link), {once: true});
    link.addEventListener("error", () => resolve(link), {once: true});
    document.head.append(link);
  });

  const loadScript = (path) => new Promise((resolve) => {
    const src = `/static/${path}${version}`;
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    script.addEventListener("load", () => resolve(script), {once: true});
    script.addEventListener("error", () => resolve(script), {once: true});
    document.head.append(script);
  });

  const tour = document.getElementById("onboarding-tour");
  if (tour) {
    // The server-rendered copy intentionally stays generic. Do not paint it with a
    // stale step count while the dedicated 0.8 tour controller is loading.
    tour.hidden = true;
    loadStyle("onboarding-tour.css");
    loadStyle("onboarding-tour-inspector-preview.css");
    loadStyle("onboarding-tour-mobile-polish.css");
    loadScript("onboarding-tour.js")
      .then(() => loadScript("onboarding-tour-inspector-preview.js"))
      .then(() => loadScript("onboarding-tour-mobile-polish.js"));
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
