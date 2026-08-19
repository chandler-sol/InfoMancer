(() => {
  const current = document.currentScript;
  const version = current?.src ? new URL(current.src).search : "";

  /* The account rail uses the canonical avatar endpoint as a real image. Keep the
     server-rendered symbol in place until the image has actually decoded so a slow
     avatar request never produces an empty circle. */
  const accountAvatar = document.querySelector(".account-avatar");
  if (accountAvatar) {
    const fallback = accountAvatar.textContent.trim() || "?";
    const avatarImage = document.createElement("img");
    avatarImage.className = "account-avatar-image";
    avatarImage.alt = "";
    avatarImage.decoding = "async";
    avatarImage.src = `/account/avatar/current?v=${Date.now()}`;
    avatarImage.style.width = "100%";
    avatarImage.style.height = "100%";
    avatarImage.style.display = "block";
    avatarImage.style.objectFit = "cover";
    avatarImage.style.borderRadius = "inherit";
    avatarImage.addEventListener("load", () => {
      if (accountAvatar.dataset.profileAvatarPreview === "1") return;
      accountAvatar.style.removeProperty("background-image");
      accountAvatar.replaceChildren(avatarImage);
      accountAvatar.dataset.profileAvatarKind = "image";
    }, {once: true});
    avatarImage.addEventListener("error", () => {
      if (accountAvatar.dataset.profileAvatarPreview === "1") return;
      accountAvatar.style.removeProperty("background-image");
      delete accountAvatar.dataset.profileAvatarKind;
      accountAvatar.textContent = fallback;
    }, {once: true});
  }

  const assetUrl = (path) => `/static/${path}${version}`;

  const loadStyle = (path) => new Promise((resolve) => {
    const href = assetUrl(path);
    const absolute = new URL(href, window.location.href).href;
    const existing = [...document.querySelectorAll('link[rel="stylesheet"]')]
      .find(link => link.href === absolute);
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
    link.fetchPriority = "high";
    link.addEventListener("load", () => resolve(link), {once: true});
    link.addEventListener("error", () => resolve(link), {once: true});
    document.head.append(link);
  });

  const loadScript = (path) => new Promise((resolve) => {
    const src = assetUrl(path);
    const absolute = new URL(src, window.location.href).href;
    const existing = [...document.scripts].find(script => script.src === absolute);
    if (existing) {
      if (existing.dataset.infomancerLoaded === "1") resolve(existing);
      else {
        existing.addEventListener("load", () => resolve(existing), {once: true});
        existing.addEventListener("error", () => resolve(existing), {once: true});
      }
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = false;
    script.addEventListener("load", () => {
      script.dataset.infomancerLoaded = "1";
      resolve(script);
    }, {once: true});
    script.addEventListener("error", () => resolve(script), {once: true});
    document.head.append(script);
  });

  const loadScriptsSequentially = async (paths) => {
    for (const path of paths) await loadScript(path);
  };

  /* Start all applicable CSS requests immediately. Layout-affecting JavaScript is
     intentionally held until its matching styles settle, which removes the race
     where a controller rearranged a surface a frame before its CSS arrived. */
  const globalStyles = Promise.all([
    loadStyle("task-widget.css"),
    loadStyle("app-navigation.css"),
    loadStyle("action-menu.css"),
  ]);

  const settingsSystem = Boolean(document.querySelector('.settings-jump-nav'));
  const savedViews = Boolean(document.querySelector('.saved-view-bar') && document.querySelector('.catalog-tabs'));
  const letterJump = Boolean(document.querySelector('.library-display-toolbar .alphabet'));
  const library = Boolean(document.querySelector('.library-table') && document.getElementById('cover-library'));
  const detail = Boolean(document.querySelector('.media-dossier'));
  const review = Boolean(document.querySelector('.review-workspace'));

  const settingsStyles = settingsSystem ? Promise.all([loadStyle("settings-system-nav.css")]) : Promise.resolve();
  const savedViewStyles = savedViews ? Promise.all([loadStyle("library-saved-views-polish.css")]) : Promise.resolve();
  const letterJumpStyles = letterJump ? Promise.all([loadStyle("library-letter-jump.css")]) : Promise.resolve();
  const libraryStyles = library ? Promise.all([
    loadStyle("library-performance.css"),
    loadStyle("library-selection-polish.css"),
    loadStyle("library-selection-toolbar.css"),
  ]) : Promise.resolve();
  const detailStyles = detail ? Promise.all([loadStyle("detail-page-polish.css")]) : Promise.resolve();
  const reviewStyles = review ? Promise.all([loadStyle("review-queue-polish.css")]) : Promise.resolve();

  /* Sidebar geometry is restored synchronously by base.html. Do not turn motion
     back on until the navigation/action chrome CSS has actually settled. On slower
     machines this prevents the final sidebar rules from arriving after animation
     has already been enabled and replaying a visible correction. */
  if (document.body?.classList.contains("has-app-sidebar")) {
    globalStyles.then(() => requestAnimationFrame(() => requestAnimationFrame(() => {
      document.body.classList.add("sidebar-motion-ready");
    })));
  }

  globalStyles.then(() => loadScriptsSequentially([
    "workspace-ui-core.js",
    "task-widget.js",
    "app-navigation.js",
  ]));

  settingsStyles.then(() => {
    if (settingsSystem) return loadScript("settings-system-nav.js");
  });
  savedViewStyles.then(() => {
    if (savedViews) return loadScript("library-saved-views-polish.js");
  });
  letterJumpStyles.then(() => {
    if (letterJump) return loadScript("library-letter-jump.js");
  });
  libraryStyles.then(() => {
    if (!library) return;
    return loadScriptsSequentially([
      "library-surface-lazy.js",
      "library-selection-polish.js",
      "library-inspector-lifecycle.js",
      "library-selection-toolbar.js",
    ]);
  });
  detailStyles.then(() => {
    if (detail) return loadScript("detail-page-polish.js");
  });
  reviewStyles.then(() => undefined);
})();
