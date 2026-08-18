(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const alphabet = toolbar?.querySelector('.alphabet');
  if (!toolbar || !alphabet || toolbar.querySelector('.letter-jump-menu')) return;

  const listSurface = document.querySelector('.library-table');
  const coverSurface = document.getElementById('cover-library');
  const state = document.getElementById('search-state');
  const responseCache = new Map();
  const cacheOrder = [];
  const MAX_CACHE = 10;

  const active = alphabet.querySelector('a.active');
  const currentLetter = active?.textContent?.trim() || 'All';

  const menu = document.createElement('details');
  menu.className = 'letter-jump-menu';

  const summary = document.createElement('summary');
  summary.setAttribute('aria-label', `Jump to titles beginning with ${currentLetter}`);

  const label = document.createElement('span');
  label.className = 'letter-jump-label';
  label.textContent = 'Jump to';

  const current = document.createElement('strong');
  current.className = 'letter-jump-current';
  current.textContent = currentLetter;

  summary.append(label, current);

  const panel = document.createElement('div');
  panel.className = 'letter-jump-panel';

  const heading = document.createElement('span');
  heading.className = 'letter-jump-heading';
  heading.textContent = 'Jump to title';

  alphabet.classList.add('letter-jump-grid');
  panel.append(heading, alphabet);
  menu.append(summary, panel);
  toolbar.prepend(menu);
  toolbar.classList.add('has-letter-jump');

  const currentView = () => coverSurface && !coverSurface.hidden ? 'covers' : 'list';

  const cacheKey = (href, view) => `${view}:${new URL(href, window.location.origin).pathname}${new URL(href, window.location.origin).search}`;

  const rememberCache = (key, value) => {
    if (!responseCache.has(key)) cacheOrder.push(key);
    responseCache.set(key, value);
    while (cacheOrder.length > MAX_CACHE) {
      const oldest = cacheOrder.shift();
      responseCache.delete(oldest);
    }
  };

  const fetchLetter = (href, view) => {
    const key = cacheKey(href, view);
    if (responseCache.has(key)) return responseCache.get(key);
    const request = fetch(href, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'X-InfoMancer-Partial': 'library',
        'X-InfoMancer-Library-View': view,
      },
    }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    }).catch(error => {
      responseCache.delete(key);
      throw error;
    });
    rememberCache(key, request);
    return request;
  };

  const setActiveLetter = (link) => {
    alphabet.querySelectorAll('a').forEach(item => item.classList.toggle('active', item === link));
    const nextLetter = link.textContent.trim() || 'All';
    current.textContent = nextLetter;
    summary.setAttribute('aria-label', `Jump to titles beginning with ${nextLetter}`);
  };

  const applyResponse = (html, href, view) => {
    const fresh = new DOMParser().parseFromString(html, 'text/html');
    if (view === 'covers') {
      const replacement = fresh.getElementById('cover-library');
      if (!replacement || !coverSurface) throw new Error('Cover surface was not returned');
      coverSurface.replaceChildren(...replacement.childNodes);
      if (listSurface) listSurface.dataset.librarySurfacePlaceholder = 'list';
    } else {
      const replacement = fresh.querySelector('.library-table');
      const replacementHead = replacement?.querySelector('thead');
      const replacementBody = replacement?.querySelector('tbody');
      const currentTable = listSurface?.querySelector('table');
      const currentBody = currentTable?.querySelector('tbody');
      if (!replacementBody || !currentTable || !currentBody) throw new Error('List surface was not returned');
      const currentHead = currentTable.querySelector('thead');
      if (replacementHead) {
        if (currentHead) currentHead.replaceWith(replacementHead.cloneNode(true));
        else currentTable.prepend(replacementHead.cloneNode(true));
      }
      currentBody.replaceChildren(...replacementBody.childNodes);
      if (coverSurface) coverSurface.dataset.librarySurfacePlaceholder = 'covers';
    }

    const nextUrl = new URL(href, window.location.origin);
    history.replaceState(history.state, '', nextUrl.pathname + nextUrl.search);
    document.dispatchEvent(new CustomEvent('infomancer:library-results-updated'));
  };

  const jumpToLetter = async (link) => {
    const href = link.href;
    const view = currentView();
    const wasActive = link.classList.contains('active');
    setActiveLetter(link);
    menu.removeAttribute('open');
    if (wasActive) return;

    menu.classList.add('loading');
    if (state) state.textContent = 'Updating library…';
    try {
      const html = await fetchLetter(href, view);
      applyResponse(html, href, view);
      if (state) state.textContent = '';
    } catch (_error) {
      window.location.assign(href);
    } finally {
      menu.classList.remove('loading');
    }
  };

  alphabet.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    event.preventDefault();
    jumpToLetter(link);
  });

  const warmLetter = event => {
    const link = event.target.closest('a');
    if (!link || link.classList.contains('active')) return;
    fetchLetter(link.href, currentView()).catch(() => {});
  };
  alphabet.addEventListener('pointerover', warmLetter);
  alphabet.addEventListener('focusin', warmLetter);

  document.addEventListener('click', event => {
    if (menu.open && !menu.contains(event.target)) menu.removeAttribute('open');
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.removeAttribute('open');
      summary.focus();
    }
  });
})();
