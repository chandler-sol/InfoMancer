(() => {
  if (!document.body?.classList.contains('role-librarian')) return;
  const actions = document.getElementById('library-selection-actions');
  if (!actions) return;

  const selectedIds = () => {
    const ids = new Set();
    document.querySelectorAll('.library-title-choice:checked').forEach((choice) => {
      if (/^\d+$/.test(String(choice.value || ''))) ids.add(String(choice.value));
    });
    return [...ids];
  };

  const install = () => {
    const primary = actions.querySelector('.library-selection-primary');
    if (!primary || primary.querySelector('[data-bulk-add-collection]')) return false;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button library-selection-command';
    button.dataset.bulkAddCollection = '';
    button.innerHTML = '<span class="library-selection-command-icon" aria-hidden="true">＋</span><span>Add to Collection</span>';
    button.title = 'Add selected titles to one or more Collections';
    button.setAttribute('aria-label', 'Add selected titles to one or more Collections');

    button.addEventListener('click', () => {
      const ids = selectedIds();
      if (ids.length < 2) return;
      const form = document.createElement('form');
      form.method = 'post';
      form.action = '/titles/collections-bulk';
      form.hidden = true;

      const csrf = document.body.dataset.csrfToken || '';
      if (csrf) {
        const token = document.createElement('input');
        token.type = 'hidden';
        token.name = 'csrf_token';
        token.value = csrf;
        form.append(token);
      }
      ids.forEach((id) => {
        const selected = document.createElement('input');
        selected.type = 'hidden';
        selected.name = 'selected';
        selected.value = id;
        form.append(selected);
      });
      const returnTo = document.createElement('input');
      returnTo.type = 'hidden';
      returnTo.name = 'return_to';
      returnTo.value = window.location.pathname + window.location.search;
      form.append(returnTo);
      document.body.append(form);
      form.submit();
    });

    const organize = [...primary.children].find((item) => item.textContent?.trim() === 'Organize');
    if (organize?.nextSibling) primary.insertBefore(button, organize.nextSibling);
    else primary.append(button);
    return true;
  };

  if (install()) return;
  const observer = new MutationObserver(() => {
    if (install()) observer.disconnect();
  });
  observer.observe(actions, {childList: true, subtree: true});
})();
