(() => {
  const closeDeleteConfirmation = (control, restoreFocus = true) => {
    const footer = control.closest('.admin-user-action-footer');
    if (!footer) return;
    const panel = footer.querySelector('.admin-user-delete-confirm');
    const toggle = footer.querySelector('.admin-user-delete-toggle');
    if (!panel || !toggle) return;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) toggle.focus();
  };

  const openDeleteConfirmation = (toggle) => {
    const footer = toggle.closest('.admin-user-action-footer');
    if (!footer) return;
    const panel = footer.querySelector('.admin-user-delete-confirm');
    if (!panel) return;

    document.querySelectorAll('.admin-user-delete-confirm:not([hidden])').forEach((openPanel) => {
      const openFooter = openPanel.closest('.admin-user-action-footer');
      const openToggle = openFooter?.querySelector('.admin-user-delete-toggle');
      openPanel.hidden = true;
      openToggle?.setAttribute('aria-expanded', 'false');
    });

    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
  };

  document.addEventListener('click', (event) => {
    const toggle = event.target.closest('.admin-user-delete-toggle');
    if (toggle) {
      event.preventDefault();
      if (toggle.getAttribute('aria-expanded') === 'true') closeDeleteConfirmation(toggle, false);
      else openDeleteConfirmation(toggle);
      return;
    }

    const cancel = event.target.closest('.admin-user-delete-cancel');
    if (cancel) {
      event.preventDefault();
      closeDeleteConfirmation(cancel);
      return;
    }

    const copyButton = event.target.closest('#copy-invitation');
    if (!copyButton) return;
    const input = document.getElementById('invitation-url');
    if (!input) return;

    navigator.clipboard.writeText(input.value).then(() => {
      copyButton.textContent = 'Copied';
    }).catch(() => {
      input.select();
      copyButton.textContent = 'Press Ctrl+C to copy';
    });
  });
})();
