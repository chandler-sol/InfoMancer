(() => {
  // workspace-core.js owns the Library/Inspector runtime. Title detail behavior is
  // deliberately isolated so hot refreshes and workflow overlays have one owner.
  const loaderScript = document.currentScript;
  let assetQuery = "";
  if (loaderScript?.src) {
    try {
      assetQuery = new URL(loaderScript.src, window.location.href).search || "";
    } catch (_error) {}
  }

  const assetUrl = (path) => `/static/${path}${assetQuery}`;

  const ensureStyle = (path, marker) => new Promise((resolve) => {
    const selector = `link[data-${marker}]`;
    const existing = document.querySelector(selector);
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
    link.href = assetUrl(path);
    link.fetchPriority = "high";
    link.dataset[marker.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = "1";
    link.addEventListener("load", () => resolve(link), {once: true});
    link.addEventListener("error", () => resolve(link), {once: true});
    document.head.append(link);
  });

  const loadScript = (path, marker) => new Promise((resolve) => {
    const selector = `script[data-${marker}]`;
    const existing = document.querySelector(selector);
    if (existing) {
      if (existing.dataset.infomancerLoaded === "1") resolve(existing);
      else {
        existing.addEventListener("load", () => resolve(existing), {once: true});
        existing.addEventListener("error", () => resolve(existing), {once: true});
      }
      return;
    }
    const script = document.createElement("script");
    script.src = assetUrl(path);
    script.async = false;
    script.dataset[marker.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = "1";
    script.addEventListener("load", () => {
      script.dataset.infomancerLoaded = "1";
      resolve(script);
    }, {once: true});
    script.addEventListener("error", () => resolve(script), {once: true});
    document.head.append(script);
  });

  const coreReady = loadScript("workspace-core.js", "workspace-core");

  // Title detail is completely idle outside /titles/<id>. Start its stylesheet
  // immediately, but do not execute the interaction layer until both CSS and the
  // shared Workspace runtime are ready. This removes a common one-frame layout race.
  if (document.querySelector(".media-dossier")) {
    const styleReady = ensureStyle("title-detail-ux.css", "title-detail-ux");
    Promise.all([coreReady, styleReady]).then(() => (
      loadScript("title-detail-ux.js", "title-detail-ux")
    ));
  }
})();
