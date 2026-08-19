(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const actions = document.getElementById('library-selection-actions');
  if (!toolbar || !actions) return;

  const viewToolbar = toolbar.querySelector('.library-view-toolbar');
  if (viewToolbar) toolbar.insertBefore(actions, viewToolbar);
  else toolbar.append(actions);
  toolbar.classList.add('library-selection-toolbar-ready');

  const sync = () => {
    toolbar.classList.toggle('has-selection-actions', !actions.hidden);
  };

  new MutationObserver(sync).observe(actions, {
    attributes: true,
    attributeFilter: ['hidden'],
  });
  sync();
})();
