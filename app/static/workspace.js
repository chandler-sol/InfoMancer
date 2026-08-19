(() => {
  // workspace-core.js owns the Library/Inspector runtime. Title detail behavior is
  // deliberately isolated in title-detail-ux.js so hot refreshes, workflow overlays,
  // and background title actions have one owner instead of accumulating here.
  const loaderScript = document.currentScript;
  let assetQuery = "";
  if (loaderScript?.src) {
    try {
      assetQuery = new URL(loaderScript.src, window.location.href).search || "";
    } catch (_error) {}
  }

  const ensureStyle = (path, marker) => {
    if (document.querySelector(`link[data-${marker}]`)) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `/static/${path}${assetQuery}`;
    link.dataset[marker.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = "1";
    document.head.append(link);
  };

  const loadScript = (path, marker) => {
    if (document.querySelector(`script[data-${marker}]`)) return;
    const script = document.createElement("script");
    script.src = `/static/${path}${assetQuery}`;
    script.async = false;
    script.dataset[marker.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = "1";
    document.head.append(script);
  };

  loadScript("workspace-core.js", "workspace-core");

  // These files are sizeable and completely idle outside /titles/<id>. Avoid
  // downloading and parsing them on Dashboard, Library, Review, Settings, etc.
  if (document.querySelector(".media-dossier")) {
    ensureStyle("title-detail-ux.css", "title-detail-ux");
    loadScript("title-detail-ux.js", "title-detail-ux");
  }
})();
