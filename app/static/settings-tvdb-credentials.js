(() => {
  const dialog = document.getElementById('tvdb-credential-dialog');
  const manage = document.getElementById('tvdb-credentials-manage');
  const form = document.getElementById('tvdb-credential-form');
  const statusLine = document.getElementById('tvdb-credential-status');
  if (!dialog || !manage || !form || !statusLine) return;

  const close = () => {
    if (dialog.open) dialog.close();
  };
  manage.addEventListener('click', () => {
    statusLine.textContent = '';
    statusLine.className = 'tvdb-credential-status';
    dialog.showModal();
    form.querySelector('input')?.focus();
  });
  dialog.querySelector('.tvdb-dialog-close')?.addEventListener('click', close);
  dialog.querySelector('[data-tvdb-cancel]')?.addEventListener('click', close);
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) close();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    if (!submit) return;
    submit.disabled = true;
    submit.textContent = 'Testing…';
    statusLine.className = 'tvdb-credential-status';
    statusLine.textContent = 'Checking these credentials with TheTVDB…';
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-CSRF-Token': document.body.dataset.csrfToken || '',
          'X-InfoMancer-Async': '1',
        },
        body: new FormData(form),
      });
      let result = {};
      try { result = await response.json(); } catch (_error) {}
      if (!response.ok || !result.ok) {
        throw new Error(result.detail || `TVDB connection test failed (${response.status}).`);
      }
      statusLine.classList.add('success');
      statusLine.textContent = result.detail || 'TVDB credentials verified and saved securely.';
      const state = document.getElementById('tvdb-settings-state');
      state?.classList.remove('warn');
      state?.classList.add('good');
      if (state) state.textContent = 'Configured';
      const keyHint = document.getElementById('tvdb-key-hint');
      const pinState = document.getElementById('tvdb-pin-state');
      if (keyHint && result.key_hint) keyHint.textContent = result.key_hint;
      if (pinState) pinState.textContent = result.pin_configured ? 'Configured' : 'Not configured';
      form.reset();
      window.setTimeout(close, 650);
    } catch (error) {
      statusLine.classList.add('error');
      statusLine.textContent = error instanceof Error ? error.message : String(error);
    } finally {
      submit.disabled = false;
      submit.textContent = 'Test & save';
    }
  });
})();
