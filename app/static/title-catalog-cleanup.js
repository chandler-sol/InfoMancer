(() => {
  const normalizeMovieCatalogFacts = () => {
    const dossier = document.querySelector(".media-dossier.detail-kind-movie");
    const line = dossier?.querySelector(".detail-hero-catalog-line");
    if (!line) return;

    const first = line.querySelector(":scope > span");
    if (first?.textContent?.trim().toLowerCase() === "released") {
      first.remove();
    }
  };

  const install = () => {
    const dossier = document.querySelector(".media-dossier.detail-kind-movie");
    if (!dossier) return;

    normalizeMovieCatalogFacts();
    const observer = new MutationObserver(normalizeMovieCatalogFacts);
    observer.observe(dossier, {childList: true, subtree: true});

    window.addEventListener("infomancer:title-detail-updated", normalizeMovieCatalogFacts);
    window.addEventListener("infomancer:title-media-updated", normalizeMovieCatalogFacts);
    window.addEventListener("pagehide", () => observer.disconnect(), {once: true});
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, {once: true});
  } else {
    install();
  }
})();
