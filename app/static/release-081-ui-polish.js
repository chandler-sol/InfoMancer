(() => {
  const csrfToken = () => document.body?.dataset.csrfToken || '';

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

  const openCollectionDetailsEditor = (card) => {
    const id = card?.dataset.collectionId || '';
    if (!/^\d+$/.test(id)) return;
    const name = card.dataset.collectionName || '';
    const description = card.dataset.collectionDescription || '';
    const hasArtwork = card.dataset.hasCustomArtwork === '1';

    const dialog = makeDialog({
      className: 'collection-picker-edit-dialog',
      eyebrow: 'COLLECTION',
      title: 'Edit Collection',
      description: 'Change the Collection name, description, or custom cover. Smart rules are edited separately.',
    });
    const body = dialog.querySelector('.release-ui-dialog-body');
    const form = document.createElement('form');
    form.className = 'collection-picker-editor-form';
    form.method = 'post';
    form.action = `/collections/${id}/edit`;
    form.enctype = 'multipart/form-data';
    form.innerHTML = `
      <input type="hidden" name="csrf_token" value="${csrfToken()}">
      <label>Name<input name="name" maxlength="80" required></label>
      <label>Description<textarea name="description" maxlength="1000" rows="4"></textarea></label>
      <label class="collection-picker-artwork-field">Custom cover<input type="file" name="artwork" accept="image/jpeg,image/png,image/webp"><small>JPEG, PNG, or WebP, up to 5 MB. A new upload replaces the current custom cover.</small></label>
      ${hasArtwork ? '<label class="inline-check collection-picker-artwork-field"><input type="checkbox" name="remove_artwork" value="1"> Remove the custom cover and return to automatic artwork</label>' : ''}
      <div class="actions"><button type="button" class="button" data-cancel>Cancel</button><button class="button primary">Save Collection</button></div>`;
    form.elements.name.value = name;
    form.elements.description.value = description;
    form.querySelector('[data-cancel]')?.addEventListener('click', () => dialog.close());
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      if (submitter) {
        submitter.disabled = true;
        submitter.setAttribute('aria-busy', 'true');
      }
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          credentials: 'same-origin',
          redirect: 'follow',
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        window.location.reload();
      } catch (error) {
        if (submitter) {
          submitter.disabled = false;
          submitter.removeAttribute('aria-busy');
        }
        window.alert(`Collection could not be saved: ${error.message}`);
      }
    });
    body.append(form);
    dialog.addEventListener('close', () => dialog.remove(), {once: true});
    dialog.showModal();
    requestAnimationFrame(() => form.elements.name?.focus());
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

    document.addEventListener('click', (event) => {
      const edit = event.target.closest('[data-collection-edit]');
      if (!edit) return;
      const card = edit.closest('.collection-picker-card');
      if (!card) return;
      event.preventDefault();
      card.querySelector('.collection-picker-menu')?.removeAttribute('open');
      openCollectionDetailsEditor(card);
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
