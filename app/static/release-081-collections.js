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

  /* Library cover view relies on a fine-pointer hover contract. Chromium honors the
     same CSS on Collections, but packaged WebView2 can occasionally fail to promote
     the parent article's :hover state until mouse-down. Mirror the Library behavior
     with pointerenter/pointerleave as a fallback while leaving native :hover intact. */
  const finePointer = window.matchMedia?.('(hover: hover) and (pointer: fine)');
  if (finePointer?.matches) {
    document.querySelectorAll('.collection-picker-card').forEach((card) => {
      card.addEventListener('pointerenter', (event) => {
        if (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen') return;
        card.classList.add('library-hover-match');
      });
      card.addEventListener('pointerleave', () => {
        card.classList.remove('library-hover-match');
      });
    });
  }

  document.addEventListener('click', (event) => {
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
