(() => {
  const tabs = document.querySelector('.catalog-tabs');
  const bar = document.querySelector('.saved-view-bar');
  if (!tabs || !bar) return;

  const manager = bar.querySelector('.saved-view-manager');
  if (!manager) return;

  const summary = manager.querySelector(':scope > summary');
  if (summary) {
    const existingCount = summary.querySelector('span')?.textContent?.trim() || '';
    summary.classList.remove('button');
    summary.replaceChildren(document.createTextNode('Saved Views'));
    if (existingCount) {
      const count = document.createElement('span');
      count.className = 'catalog-saved-view-count';
      count.textContent = existingCount;
      summary.append(count);
    }
    summary.setAttribute('aria-label', existingCount ? `Saved Views, ${existingCount}` : 'Saved Views, none saved');
  }

  manager.classList.add('catalog-saved-views');
  tabs.append(manager);

  const panel = manager.querySelector('.saved-view-panel');
  const list = manager.querySelector('.saved-view-list');
  if (panel) {
    const explainer = document.createElement('div');
    explainer.className = 'saved-view-explainer';
    explainer.innerHTML = '<strong>Save the Library exactly as you have it</strong><span>A Saved View remembers the current Library scope, filters, and sorting. Pin it to keep a shortcut in Library and on Dashboard.</span>';
    panel.prepend(explainer);

    if (!list) {
      const empty = document.createElement('div');
      empty.className = 'saved-view-inline-empty';
      empty.innerHTML = '<strong>No saved views yet</strong><span>Your first saved view will appear here.</span>';
      explainer.after(empty);
    }
  }

  /* <details> is intentionally used for progressive enhancement, but native details
     elements do not dismiss when the user clicks elsewhere. Treat this one like the
     rest of InfoMancer's popovers: outside pointer-down and Escape both close it. */
  const closeManager = ({restoreFocus = false} = {}) => {
    if (!manager.open) return;
    manager.open = false;
    if (restoreFocus) summary?.focus();
  };

  document.addEventListener('pointerdown', (event) => {
    if (manager.open && !manager.contains(event.target)) closeManager();
  }, true);

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !manager.open) return;
    event.preventDefault();
    closeManager({restoreFocus: true});
  });

  bar.classList.add('saved-view-integrated');
  bar.hidden = true;
})();
