(() => {
  const current = document.currentScript;
  const version = current?.src ? new URL(current.src).search : "";

  /* The account rail used to rely on a text glyph plus page-specific background
     image overrides. Desktop sidebar CSS uses a background shorthand on the avatar
     circle, which can legitimately wipe out those background images. Render the
     canonical avatar endpoint as a real child image instead. It works for initials,
     built-in glyphs, and uploaded icons because the endpoint already owns all three
     representations. Keep the server-rendered symbol in place until the image has
     actually loaded so this fails visibly rather than becoming an empty circle. */
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

  /* Sidebar width/collapse state is restored synchronously by base.html. Keep the
     matching CSS transitions suppressed through the first stable paint, then turn
     them back on for actual user interaction. This prevents full-page navigation
     from looking like the application shell slides sideways into position. */
  if (document.body?.classList.contains("has-app-sidebar")) {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.body.classList.add("sidebar-motion-ready");
    }));
  }

  const styles = document.createElement("link");
  styles.rel = "stylesheet";
  styles.href = `/static/task-widget.css${version}`;

  const navigationStyles = document.createElement("link");
  navigationStyles.rel = "stylesheet";
  navigationStyles.href = `/static/app-navigation.css${version}`;

  const actionMenuStyles = document.createElement("link");
  actionMenuStyles.rel = "stylesheet";
  actionMenuStyles.href = `/static/action-menu.css${version}`;

  document.head.append(styles, navigationStyles, actionMenuStyles);

  const core = document.createElement("script");
  core.src = `/static/workspace-ui-core.js${version}`;
  core.async = false;

  const tasks = document.createElement("script");
  tasks.src = `/static/task-widget.js${version}`;
  tasks.async = false;

  const navigation = document.createElement("script");
  navigation.src = `/static/app-navigation.js${version}`;
  navigation.async = false;

  document.head.append(core, tasks, navigation);

  /* Settings polish now lives in settings.css and the nav labels are rendered
     correctly by the server. Keeping those rules in the render-blocking stylesheet
     prevents the Settings workspace from visibly reflowing after each page load. */

  if (document.querySelector('.settings-jump-nav')) {
    const systemNavStyles = document.createElement('link');
    systemNavStyles.rel = 'stylesheet';
    systemNavStyles.href = `/static/settings-system-nav.css${version}`;

    const systemNav = document.createElement('script');
    systemNav.src = `/static/settings-system-nav.js${version}`;
    systemNav.async = false;

    document.head.append(systemNavStyles, systemNav);
  }

  if (document.querySelector('.saved-view-bar') && document.querySelector('.catalog-tabs')) {
    const savedViewStyles = document.createElement('link');
    savedViewStyles.rel = 'stylesheet';
    savedViewStyles.href = `/static/library-saved-views-polish.css${version}`;

    const savedViewPolish = document.createElement('script');
    savedViewPolish.src = `/static/library-saved-views-polish.js${version}`;
    savedViewPolish.async = false;

    document.head.append(savedViewStyles, savedViewPolish);
  }

  if (document.querySelector('.library-display-toolbar .alphabet')) {
    const letterJumpStyles = document.createElement('link');
    letterJumpStyles.rel = 'stylesheet';
    letterJumpStyles.href = `/static/library-letter-jump.css${version}`;

    const letterJump = document.createElement('script');
    letterJump.src = `/static/library-letter-jump.js${version}`;
    letterJump.async = false;

    document.head.append(letterJumpStyles, letterJump);
  }

  if (document.querySelector('.library-table') && document.getElementById('cover-library')) {
    const libraryStyles = document.createElement('link');
    libraryStyles.rel = 'stylesheet';
    libraryStyles.href = `/static/library-performance.css${version}`;

    const librarySelectionStyles = document.createElement('link');
    librarySelectionStyles.rel = 'stylesheet';
    librarySelectionStyles.href = `/static/library-selection-polish.css${version}`;

    const librarySelectionToolbarStyles = document.createElement('link');
    librarySelectionToolbarStyles.rel = 'stylesheet';
    librarySelectionToolbarStyles.href = `/static/library-selection-toolbar.css${version}`;

    const librarySurface = document.createElement('script');
    librarySurface.src = `/static/library-surface-lazy.js${version}`;
    librarySurface.async = false;

    const librarySelection = document.createElement('script');
    librarySelection.src = `/static/library-selection-polish.js${version}`;
    librarySelection.async = false;

    const libraryInspectorLifecycle = document.createElement('script');
    libraryInspectorLifecycle.src = `/static/library-inspector-lifecycle.js${version}`;
    libraryInspectorLifecycle.async = false;

    const librarySelectionToolbar = document.createElement('script');
    librarySelectionToolbar.src = `/static/library-selection-toolbar.js${version}`;
    librarySelectionToolbar.async = false;

    document.head.append(
      libraryStyles,
      librarySelectionStyles,
      librarySelectionToolbarStyles,
      librarySurface,
      librarySelection,
      libraryInspectorLifecycle,
      librarySelectionToolbar,
    );
  }

  if (document.querySelector('.media-dossier')) {
    const detailStyles = document.createElement('link');
    detailStyles.rel = 'stylesheet';
    detailStyles.href = `/static/detail-page-polish.css${version}`;

    const detailPolish = document.createElement('script');
    detailPolish.src = `/static/detail-page-polish.js${version}`;
    detailPolish.async = false;

    document.head.append(detailStyles, detailPolish);
  }

  if (document.querySelector('.review-workspace')) {
    const reviewQueueStyles = document.createElement('link');
    reviewQueueStyles.rel = 'stylesheet';
    reviewQueueStyles.href = `/static/review-queue-polish.css${version}`;
    document.head.append(reviewQueueStyles);
  }
})();
