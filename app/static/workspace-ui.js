(() => {
  const current = document.currentScript;
  const version = current?.src ? new URL(current.src).search : '';

  const accountAvatar = document.querySelector('.account-avatar');
  if (accountAvatar) {
    const fallback = accountAvatar.textContent.trim() || '?';
    const avatarImage = document.createElement('img');
    avatarImage.className = 'account-avatar-image';
    avatarImage.alt = '';
    avatarImage.decoding = 'async';
    avatarImage.src = '/account/avatar/current';
    avatarImage.style.width = '100%';
    avatarImage.style.height = '100%';
    avatarImage.style.display = 'block';
    avatarImage.style.objectFit = 'cover';
    avatarImage.style.borderRadius = 'inherit';
    avatarImage.addEventListener('load', () => {
      if (accountAvatar.dataset.profileAvatarPreview === '1') return;
      accountAvatar.style.removeProperty('background-image');
      accountAvatar.replaceChildren(avatarImage);
      accountAvatar.dataset.profileAvatarKind = 'image';
    }, {once: true});
    avatarImage.addEventListener('error', () => {
      if (accountAvatar.dataset.profileAvatarPreview === '1') return;
      accountAvatar.style.removeProperty('background-image');
      delete accountAvatar.dataset.profileAvatarKind;
      accountAvatar.textContent = fallback;
    }, {once: true});
  }

  const assetUrl = (path) => `/static/${path}${version}`;

  const loadStyle = (path) => new Promise((resolve) => {
    const href = assetUrl(path);
    const absolute = new URL(href, window.location.href).href;
    const existing = [...document.querySelectorAll('link[rel="stylesheet"]')]
      .find((link) => link.href === absolute);
    if (existing) {
      if (existing.sheet) resolve(existing);
      else {
        existing.addEventListener('load', () => resolve(existing), {once: true});
        existing.addEventListener('error', () => resolve(existing), {once: true});
      }
      return;
    }
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.fetchPriority = 'high';
    link.addEventListener('load', () => resolve(link), {once: true});
    link.addEventListener('error', () => resolve(link), {once: true});
    document.head.append(link);
  });

  const loadScript = (path) => new Promise((resolve) => {
    const src = assetUrl(path);
    const absolute = new URL(src, window.location.href).href;
    const existing = [...document.scripts].find((script) => script.src === absolute);
    if (existing) {
      if (existing.dataset.infomancerLoaded === '1') resolve(existing);
      else {
        existing.addEventListener('load', () => resolve(existing), {once: true});
        existing.addEventListener('error', () => resolve(existing), {once: true});
      }
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.addEventListener('load', () => {
      script.dataset.infomancerLoaded = '1';
      resolve(script);
    }, {once: true});
    script.addEventListener('error', () => resolve(script), {once: true});
    document.head.append(script);
  });

  const globalStyles = Promise.all([
    loadStyle('task-widget.css'),
    loadStyle('app-navigation.css'),
    loadStyle('action-menu.css'),
  ]);

  const settingsSystem = Boolean(document.querySelector('.settings-jump-nav'));
  const settingsCoverDensity = Boolean(document.getElementById('settings-cover-size'));
  const savedViews = Boolean(document.querySelector('.saved-view-bar') && document.querySelector('.catalog-tabs'));
  const letterJumpToolbar = document.querySelector('.library-display-toolbar');
  const letterJumpAlphabet = letterJumpToolbar?.querySelector('.alphabet');
  const libraryViewToolbar = document.querySelector('.library-view-toolbar');
  const libraryControls = document.querySelector('.library-controls');
  const filterSearch = document.getElementById('library-filter-search');
  const filterSearchInput = document.getElementById('live-library-search');
  const coverSizeControl = document.getElementById('cover-size-control');
  const letterJump = Boolean(letterJumpAlphabet);
  const library = Boolean(document.querySelector('.library-table') && document.getElementById('cover-library'));
  const detail = Boolean(document.querySelector('.media-dossier'));
  const review = Boolean(document.querySelector('.review-workspace'));

  if (letterJumpAlphabet) {
    letterJumpAlphabet.style.visibility = 'hidden';
    letterJumpAlphabet.setAttribute('aria-hidden', 'true');
    if (letterJumpToolbar) letterJumpToolbar.style.justifyContent = 'flex-start';
  }

  /* Match the enhanced filter-strip geometry during first paint. Using the old
     fit-content width here caused a brief compressed row before the late Library
     stylesheet took ownership. */
  if (libraryControls) {
    libraryControls.style.boxSizing = 'border-box';
    libraryControls.style.width = '100%';
    libraryControls.style.maxWidth = '100%';
  }
  if (filterSearch && filterSearchInput && !filterSearch.classList.contains('open')) {
    filterSearchInput.style.visibility = 'hidden';
  }

  /* Hide the server-rendered display group during the placement handoff. The
     Density controller moves it beside the Library scope tabs before it becomes
     visible, avoiding a flash in the old Starts-with row. */
  if (library && libraryViewToolbar) {
    libraryViewToolbar.style.visibility = 'hidden';
    libraryViewToolbar.setAttribute('aria-hidden', 'true');
  }
  if (library && coverSizeControl && !coverSizeControl.hidden) {
    coverSizeControl.style.visibility = 'hidden';
    coverSizeControl.setAttribute('aria-hidden', 'true');
  }

  const settingsStyles = settingsSystem
    ? Promise.all([loadStyle('settings-system-nav.css')])
    : Promise.resolve();
  const savedViewStyles = savedViews
    ? Promise.all([loadStyle('library-saved-views-polish.css')])
    : Promise.resolve();
  const letterJumpStyles = letterJump
    ? Promise.all([loadStyle('library-letter-jump.css')])
    : Promise.resolve();
  const libraryStyles = library ? Promise.all([
    loadStyle('library-controls-polish.css'),
    loadStyle('library-performance.css'),
    loadStyle('library-density.css'),
    loadStyle('library-selection-polish.css'),
    loadStyle('library-selection-toolbar.css'),
    loadStyle('library-selection-compact.css'),
  ]) : Promise.resolve();
  const detailStyles = detail
    ? Promise.all([loadStyle('detail-page-polish.css')])
    : Promise.resolve();
  const reviewStyles = review
    ? Promise.all([loadStyle('review-queue-polish.css')])
    : Promise.resolve();
  const letterJumpReady = letterJump
    ? letterJumpStyles.then(() => loadScript('library-letter-jump.js'))
    : Promise.resolve();

  /* app-shell-bootstrap.js restores sidebar geometry immediately after <body>.
     Motion is enabled only after the late chrome CSS has settled. */
  if (document.body?.classList.contains('has-app-sidebar')) {
    globalStyles.then(() => requestAnimationFrame(() => requestAnimationFrame(() => {
      document.body.classList.add('sidebar-motion-ready');
    })));
  }

  globalStyles.then(() => Promise.all([
    loadScript('workspace-ui-core.js'),
    loadScript('task-widget.js'),
    loadScript('app-navigation.js'),
  ]));

  settingsStyles.then(() => {
    if (settingsSystem) return loadScript('settings-system-nav.js');
  });
  if (settingsCoverDensity) loadScript('settings-cover-density.js');
  savedViewStyles.then(() => {
    if (savedViews) return loadScript('library-saved-views-polish.js');
  });
  letterJumpReady.then(() => {
    if (!letterJump) return;
    letterJumpAlphabet.style.removeProperty('visibility');
    letterJumpAlphabet.removeAttribute('aria-hidden');
    letterJumpToolbar?.style.removeProperty('justify-content');
  });

  libraryStyles.then(async () => {
    if (!library) return;
    libraryControls?.style.removeProperty('box-sizing');
    libraryControls?.style.removeProperty('width');
    libraryControls?.style.removeProperty('max-width');
    filterSearchInput?.style.removeProperty('visibility');

    /* The base Library controller owns filtering and selection state. Install it
       before the optional UI layers so every enhancement observes one canonical
       state source instead of racing the old template controller. */
    await loadScript('library-controller.js');
    const pending = [
      'library-density.js',
      'library-surface-lazy.js',
      'library-selection-polish.js',
      'library-inspector-lifecycle.js',
      'library-selection-toolbar.js',
    ].map((path) => loadScript(path));
    pending[0].then(() => {
      coverSizeControl?.style.removeProperty('visibility');
      coverSizeControl?.removeAttribute('aria-hidden');
      libraryViewToolbar?.style.removeProperty('visibility');
      libraryViewToolbar?.removeAttribute('aria-hidden');
    });
    return Promise.all(pending);
  });

  detailStyles.then(() => {
    if (detail) return loadScript('detail-page-polish.js');
  });
  reviewStyles.then(() => undefined);
})();
