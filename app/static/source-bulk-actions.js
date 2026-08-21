(() => {
  if (window.location.pathname !== '/sources') return;

  const scanForm = document.querySelector('.sources-global-actions form[action="/scan-all"]');
  const actions = scanForm?.closest('.sources-global-actions');
  if (!scanForm || !actions || actions.querySelector('form[action="/roots/check-all"]')) return;

  const checkForm = document.createElement('form');
  checkForm.method = 'post';
  checkForm.action = '/roots/check-all';

  const button = document.createElement('button');
  button.type = 'submit';
  button.className = 'button';
  button.textContent = 'Check all connections';
  checkForm.append(button);

  actions.insertBefore(checkForm, scanForm);
})();
