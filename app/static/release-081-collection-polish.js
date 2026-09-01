(() => {
  if (!window.location.pathname.startsWith('/collections/')) return;

  const ownText = (element) => [...element.childNodes]
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent || '')
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();

  const markSortControl = () => {
    const label = [...document.querySelectorAll('span, strong, label, small, div')]
      .find((element) => /^Sort Titles\s*:?$/i.test(ownText(element)));
    if (!label) return null;

    label.classList.add('collection-sort-label');
    const toolbar = label.parentElement;
    if (!toolbar) return null;
    toolbar.classList.add('collection-sort-toolbar');

    let ancestor = toolbar;
    while (ancestor && ancestor !== document.body) {
      ancestor.classList.add('collection-sort-overflow-scope');
      if (ancestor.tagName === 'MAIN') break;
      ancestor = ancestor.parentElement;
    }
    toolbar.querySelectorAll('details').forEach((details) => {
      details.classList.add('collection-sort-menu');
    });
    return toolbar;
  };

  let toolbar = markSortControl();
  if (!toolbar) {
    const observer = new MutationObserver(() => {
      toolbar = markSortControl();
      if (toolbar) observer.disconnect();
    });
    observer.observe(document.body, {childList: true, subtree: true});
  }

  document.addEventListener('toggle', (event) => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.open) return;
    const parent = details.closest('.collection-sort-toolbar');
    if (!parent) return;
    parent.querySelectorAll('details').forEach((other) => {
      if (other !== details) other.removeAttribute('open');
    });
    let ancestor = details.parentElement;
    while (ancestor && ancestor !== document.body) {
      ancestor.classList.add('collection-sort-overflow-scope');
      if (ancestor.tagName === 'MAIN') break;
      ancestor = ancestor.parentElement;
    }
  }, true);

  /* Picker menu links can deep-link directly into the existing manual Collection
     management UI. Wait for the page load event so collection-detail.js has already
     installed its dialog/reorder handlers before we synthesize the requested click. */
  const requestedAction = new URLSearchParams(window.location.search).get('action');
  const actionSelector = {
    add: '[data-collection-dialog-open="collection-add-dialog"]',
    edit: '[data-collection-dialog-open="collection-edit-dialog"]',
    reorder: '[data-collection-reorder-toggle]',
  }[requestedAction];

  if (actionSelector) {
    window.addEventListener('load', () => {
      const trigger = document.querySelector(actionSelector);
      if (!trigger) return;
      trigger.click();
      const clean = new URL(window.location.href);
      clean.searchParams.delete('action');
      history.replaceState(history.state, '', clean.pathname + clean.search + clean.hash);
    }, {once: true});
  }
})();
