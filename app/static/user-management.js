(() => {
  const closeDeleteConfirmation = (button) => {
    const details = button.closest('.admin-user-delete');
    if (!details) return;
    details.open = false;
    details.querySelector('.admin-user-delete-toggle')?.focus();
  };

  document.addEventListener('click', (event) => {
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
