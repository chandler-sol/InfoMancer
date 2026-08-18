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
  if (panel && !list) {
    const empty = document.createElement('div');
    empty.className = 'saved-view-inline-empty';
    empty.innerHTML = '<strong>No saved views yet</strong><span>Choose filters and sorting, then save this view for quick access here.</span>';
    panel.prepend(empty);
  }

  bar.classList.add('saved-view-integrated');
  bar.hidden = true;
})();
