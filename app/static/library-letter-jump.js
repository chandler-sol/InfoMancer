(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const alphabet = toolbar?.querySelector('.alphabet');
  if (!toolbar || !alphabet || toolbar.querySelector('.letter-jump-menu')) return;

  const listSurface = document.querySelector('.library-table');
  const coverSurface = document.getElementById('cover-library');
  const state = document.getElementById('search-state');
  const responseCache = new Map();
  const inflight = new Map();
  const cacheOrder = [];
  const MAX_CACHE = 10;
  let jumpSerial = 0;

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

  const cacheKey = (href, view) => {
    const url = new URL(href, window.location.origin);
    return `${view}:${url.pathname}${url.search}`;
  };

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
    if (responseCache.has(key)) return Promise.resolve(responseCache.get(key));
    if (inflight.has(key)) return inflight.get(key);

    const request = fetch(href, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        'X-InfoMancer-Partial': 'library',
        'X-InfoMancer-Library-View': view,
      },
    }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const html = await response.text();
      rememberCache(key, html);
      return html;
    }).finally(() => inflight.delete(key));

    inflight.set(key, request);
    return request;
  };

  const setActiveLetter = (link) => {
    alphabet.querySelectorAll('a').forEach(item => item.classList.toggle('active', item === link));
    const nextLetter = link.textContent.trim() || 'All';
    current.textContent = nextLetter;
    summary.setAttribute('aria-label', `Jump to titles beginning with ${nextLetter}`);
  };

  /* Avoid parsing the complete InfoMancer document just to replace one Library
     surface. This follows the same fragment-first path as the lazy List/Covers
     hydrator and noticeably reduces DOMParser work on large libraries. */
  const extractSurface = (html, view) => {
    const marker = view === 'covers'
      ? '<section class="cover-library" id="cover-library"'
      : '<section class="panel table-wrap library-table"';
    const start = html.indexOf(marker);
    if (start < 0) return null;
    const end = html.indexOf('</section>', start);
    if (end < 0) return null;
    const template = document.createElement('template');
    template.innerHTML = html.slice(start, end + '</section>'.length).trim();
    return template.content.firstElementChild;
  };

  const applyResponse = (html, href, view) => {
    const replacement = extractSurface(html, view);
    if (!replacement) throw new Error('Library surface was not returned');

    if (view === 'covers') {
      if (!coverSurface) throw new Error('Cover surface is unavailable');
      coverSurface.replaceChildren(...replacement.childNodes);
      if (listSurface) listSurface.dataset.librarySurfacePlaceholder = 'list';
    } else {
      const replacementHead = replacement.querySelector('thead');
      const replacementBody = replacement.querySelector('tbody');
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
    const serial = ++jumpSerial;
    setActiveLetter(link);
    menu.removeAttribute('open');
    if (wasActive) return;

    menu.classList.add('loading');
    summary.setAttribute('aria-busy', 'true');
    if (state) state.textContent = 'Updating library…';
    try {
      const html = await fetchLetter(href, view);
      if (serial !== jumpSerial) return;
      applyResponse(html, href, view);
      if (state) state.textContent = '';
    } catch (_error) {
      if (serial === jumpSerial) window.location.assign(href);
    } finally {
      if (serial === jumpSerial) {
        menu.classList.remove('loading');
        summary.removeAttribute('aria-busy');
      }
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
  alphabet.addEventListener('pointerover', warmLetter, {passive: true});
  alphabet.addEventListener('focusin', warmLetter);

  document.addEventListener('pointerdown', event => {
    if (menu.open && !menu.contains(event.target)) menu.removeAttribute('open');
  }, true);

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      event.preventDefault();
      menu.removeAttribute('open');
      summary.focus();
    }
  });

  document.addEventListener('infomancer:before-navigate', () => {
    jumpSerial += 1;
    menu.removeAttribute('open');
  });
})();
