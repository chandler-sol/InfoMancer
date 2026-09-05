(() => {
  const pickerMenus = [...document.querySelectorAll('.collection-picker-menu')];
  const closePickerMenus = (except = null) => {
    pickerMenus.forEach((details) => {
      if (details !== except) details.open = false;
    });
  };

  pickerMenus.forEach((details) => {
    details.addEventListener('toggle', () => {
      if (details.open) closePickerMenus(details);
    });
  });

  /* Match Library cover behavior on touch/coarse pointers: the first tap exposes the
     card actions, and a subsequent tap can follow the Collection link. Fine-pointer
     hover is intentionally left entirely to library.css. */
  document.addEventListener('click', (event) => {
    if (window.matchMedia('(hover: none), (pointer: coarse)').matches) {
      const coverLink = event.target.closest('.collection-picker-card .cover-card-link');
      if (coverLink) {
        const card = coverLink.closest('.cover-card');
        if (card && !card.classList.contains('actions-visible')) {
          event.preventDefault();
          document.querySelectorAll('.collection-picker-card.actions-visible')
            .forEach((other) => other.classList.remove('actions-visible'));
          card.classList.add('actions-visible');
          return;
        }
      } else if (!event.target.closest('.collection-picker-card')) {
        document.querySelectorAll('.collection-picker-card.actions-visible')
          .forEach((card) => card.classList.remove('actions-visible'));
      }
    }

    const active = event.target.closest('.collection-picker-menu');
    if (!active) closePickerMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closePickerMenus();
  });

  const params = new URLSearchParams(window.location.search);
  const operationId = params.get('undo_collection');
  if (!operationId || !/^\d+$/.test(operationId)) return;

  const current = document.getElementById('flash-message');
  if (!current) return;

  /* app-shell.js schedules the ordinary flash for removal after a few seconds.
     Replace that node so an undoable destructive action stays visible long enough
     to act on and cannot disappear underneath the fixed task center. */
  const notice = current.cloneNode(true);
  notice.classList.add('collection-undo-notice');

  const form = document.createElement('form');
  form.method = 'post';
  form.action = `/collections/deletions/${encodeURIComponent(operationId)}/undo`;
  form.className = 'collection-undo-form';

  const csrf = document.body?.dataset.csrfToken || '';
  if (csrf) {
    const token = document.createElement('input');
    token.type = 'hidden';
    token.name = 'csrf_token';
    token.value = csrf;
    form.append(token);
  }

  const button = document.createElement('button');
  button.type = 'submit';
  button.className = 'button';
  button.textContent = 'Undo';
  button.setAttribute('aria-label', 'Undo collection deletion');
  form.append(button);
  notice.append(form);
  current.replaceWith(notice);

  /* Do not leave a stale one-shot operation id in browser history. The form keeps
     the id it needs, while reload/back will not keep offering the same Undo. */
  const clean = new URL(window.location.href);
  clean.searchParams.delete('undo_collection');
  history.replaceState(history.state, '', clean.pathname + clean.search + clean.hash);
})();
