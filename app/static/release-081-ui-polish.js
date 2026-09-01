(() => {
  const makeDialog = ({className = '', eyebrow = '', title = '', description = ''} = {}) => {
    const dialog = document.createElement('dialog');
    dialog.className = `release-ui-dialog ${className}`.trim();
    dialog.innerHTML = `
      <header class="release-ui-dialog-head">
        <div>${eyebrow ? `<p class="eyebrow">${eyebrow}</p>` : ''}<h2>${title}</h2>${description ? `<p class="muted">${description}</p>` : ''}</div>
        <button type="button" class="release-ui-dialog-close" aria-label="Close">×</button>
      </header>
      <div class="release-ui-dialog-body"></div>`;
    const close = () => dialog.close();
    dialog.querySelector('.release-ui-dialog-close')?.addEventListener('click', close);
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) close();
    });
    dialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      close();
    });
    document.body.append(dialog);
    return dialog;
  };

  const collectionsIndex = () => {
    if (window.location.pathname !== '/collections') return;
    const manual = document.querySelector('.collection-create');
    const smart = document.querySelector('.smart-collection-create');
    if (!manual || !smart) return;

    document.body.classList.add('collections-modalized');
    const launcher = document.createElement('div');
    launcher.className = 'collection-create-launcher';
    launcher.innerHTML = '<button type="button" class="button primary">+ Create Collection</button>';
    manual.before(launcher);

    const dialog = makeDialog({
      className: 'collection-create-dialog',
      eyebrow: 'CURATED LIBRARY',
      title: 'Create a Collection',
      description: 'Choose a manual Collection you curate yourself or a Smart Collection that fills itself from saved Library rules.',
    });
    const body = dialog.querySelector('.release-ui-dialog-body');
    const switcher = document.createElement('div');
    switcher.className = 'release-dialog-switcher';
    switcher.innerHTML = '<button type="button" class="active" data-kind="manual">Manual</button><button type="button" data-kind="smart">Smart</button>';
    body.append(switcher);

    const manualPane = document.createElement('div');
    manualPane.className = 'release-dialog-pane';
    const smartPane = document.createElement('div');
    smartPane.className = 'release-dialog-pane';
    smartPane.hidden = true;
    manualPane.append(manual);
    smart.open = true;
    smartPane.append(smart);
    body.append(manualPane, smartPane);

    const setKind = (kind) => {
      const isSmart = kind === 'smart';
      manualPane.hidden = isSmart;
      smartPane.hidden = !isSmart;
      switcher.querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.kind === kind));
      requestAnimationFrame(() => (isSmart ? smart : manual).querySelector('input,select,textarea')?.focus());
    };
    switcher.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-kind]');
      if (button) setKind(button.dataset.kind);
    });
    launcher.querySelector('button').addEventListener('click', () => {
      setKind('manual');
      dialog.showModal();
    });

    /* Manual Collection editing from the picker stays modal instead of making the
       user visit the detail page only to open the same editor. */
    document.addEventListener('click', async (event) => {
      const link = event.target.closest('.collection-picker-menu a[href*="?action=edit"]');
      if (!link) return;
      event.preventDefault();
      try {
        const response = await fetch(link.href, {credentials: 'same-origin'});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
        const sourceDialog = parsed.getElementById('collection-edit-dialog');
        if (!sourceDialog) throw new Error('Collection editor unavailable');

        if (!document.querySelector('link[href*="collection-detail.css"]')) {
          const style = document.createElement('link');
          style.rel = 'stylesheet';
          style.href = `/static/collection-detail.css${new URL(document.currentScript?.src || window.location.href, window.location.href).search || ''}`;
          document.head.append(style);
        }
        const imported = document.importNode(sourceDialog, true);
        imported.id = `collection-edit-dialog-picker-${Date.now()}`;
        imported.querySelectorAll('[data-collection-dialog-close]').forEach((button) => button.addEventListener('click', () => imported.close()));
        imported.addEventListener('cancel', (cancelEvent) => {
          cancelEvent.preventDefault();
          imported.close();
        });
        const form = imported.querySelector('.collection-editor-form');
        form?.addEventListener('submit', async (submitEvent) => {
          submitEvent.preventDefault();
          const submitter = submitEvent.submitter;
          if (submitter) submitter.disabled = true;
          try {
            const save = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin'});
            if (!save.ok) throw new Error(`HTTP ${save.status}`);
            window.location.reload();
          } catch (error) {
            if (submitter) submitter.disabled = false;
            window.alert(`Collection could not be saved: ${error.message}`);
          }
        });
        imported.addEventListener('close', () => imported.remove(), {once: true});
        document.body.append(imported);
        imported.showModal();
      } catch (error) {
        window.location.assign(link.href);
      }
    });
  };

  const userManagement = () => {
    if (window.location.pathname !== '/admin/users') return;
    const form = document.querySelector('.create-user-form');
    const heading = document.querySelector('.user-list-heading');
    if (!form || !heading) return;
    document.body.classList.add('user-management-modalized');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'button primary';
    button.textContent = '+ Add user';
    heading.append(button);

    const dialog = makeDialog({
      className: 'user-create-dialog',
      eyebrow: 'ACCESS',
      title: 'Invite a person',
      description: 'Create a Member or Librarian account. InfoMancer will provide the appropriate setup flow for this installation.',
    });
    dialog.querySelector('.release-ui-dialog-body').append(form);
    button.addEventListener('click', () => {
      dialog.showModal();
      requestAnimationFrame(() => form.querySelector('input:not([type="hidden"])')?.focus());
    });
  };

  const collectionDetail = () => {
    if (!/^\/collections\/\d+$/.test(window.location.pathname)) return;
    const art = document.querySelector('.collection-detail-art');
    const add = document.querySelector('[data-collection-dialog-open="collection-add-dialog"]');
    if (!art || !add) return;
    const wrap = document.createElement('div');
    wrap.className = 'collection-detail-art-wrap';
    art.before(wrap);
    wrap.append(art);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'collection-detail-art-action';
    button.setAttribute('aria-label', 'Add titles to this collection');
    button.title = 'Add titles';
    button.textContent = '+';
    button.addEventListener('click', () => add.click());
    wrap.append(button);
  };

  collectionsIndex();
  userManagement();
  collectionDetail();
})();
