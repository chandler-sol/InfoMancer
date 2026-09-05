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

  const closeDialog = (dialog) => {
    if (dialog?.open) dialog.close();
  };

  const openCollectionDialog = async (ids) => {
    const request = new FormData();
    const csrf = document.body.dataset.csrfToken || '';
    if (csrf) request.append('csrf_token', csrf);
    ids.forEach((id) => request.append('selected', id));
    request.append('return_to', window.location.pathname + window.location.search + window.location.hash);

    const response = await fetch('/titles/collections-bulk', {
      method: 'POST',
      body: request,
      credentials: 'same-origin',
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
    const sourceForm = parsed.querySelector('.bulk-collection-form');
    if (!sourceForm) throw new Error('Collection picker was not available');

    const dialog = document.createElement('dialog');
    dialog.className = 'release-ui-dialog release-bulk-collection-dialog';
    dialog.innerHTML = `
      <header class="release-ui-dialog-head">
        <div><p class="eyebrow">BULK ORGANIZATION</p><h2>Add to Collection</h2><p class="muted">Add the selected titles to one or more manual Collections without leaving the Library.</p></div>
        <button type="button" class="release-ui-dialog-close" aria-label="Close Add to Collection">×</button>
      </header>
      <div class="release-ui-dialog-body"></div>`;
    const form = document.importNode(sourceForm, true);
    dialog.querySelector('.release-ui-dialog-body').append(form);

    dialog.querySelector('.release-ui-dialog-close')?.addEventListener('click', () => closeDialog(dialog));
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) closeDialog(dialog);
    });
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      closeDialog(dialog);
    });
    dialog.addEventListener('close', () => dialog.remove(), {once: true});

    form.querySelector('a[href], .button[href]')?.addEventListener('click', (event) => {
      const target = event.target.closest('a');
      if (target && /cancel/i.test(target.textContent || '')) {
        event.preventDefault();
        closeDialog(dialog);
      }
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      if (submitter) {
        submitter.disabled = true;
        submitter.setAttribute('aria-busy', 'true');
      }
      try {
        const apply = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          credentials: 'same-origin',
          redirect: 'follow',
        });
        if (!apply.ok) throw new Error(`HTTP ${apply.status}`);
        const destination = new URL(apply.url || window.location.href, window.location.href);
        if (destination.origin === window.location.origin) window.location.assign(destination.href);
        else window.location.reload();
      } catch (error) {
        if (submitter) {
          submitter.disabled = false;
          submitter.removeAttribute('aria-busy');
        }
        window.alert(`Titles could not be added to the Collection: ${error.message}`);
      }
    });

    document.body.append(dialog);
    dialog.showModal();
    requestAnimationFrame(() => dialog.querySelector('input,button,select')?.focus());
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

    button.addEventListener('click', async () => {
      const ids = selectedIds();
      if (ids.length < 2) return;
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      try {
        await openCollectionDialog(ids);
      } catch (error) {
        window.alert(`Collection picker could not be opened: ${error.message}`);
      } finally {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
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
