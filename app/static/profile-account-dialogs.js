(() => {
  const dialog = document.getElementById('profile-account-dialog');
  const shell = dialog?.querySelector('.profile-account-dialog-shell');
  const body = dialog?.querySelector('[data-profile-account-dialog-body]');
  const title = dialog?.querySelector('[data-profile-account-dialog-title]');
  const copy = dialog?.querySelector('[data-profile-account-dialog-copy]');
  const feedback = document.querySelector('[data-profile-account-feedback]');
  const triggers = [...document.querySelectorAll('[data-profile-account-dialog]')];
  if (!dialog || !shell || !body || !title || !copy || !triggers.length) return;

  const configurations = {
    password: {
      title: 'Change password',
      copy: 'Update your local sign-in password. Other browser sessions will be signed out.',
      selector: '.settings-form',
    },
    sessions: {
      title: 'Active sessions',
      copy: 'Review the browsers and devices currently signed in to your account.',
      selector: '.session-panel',
    },
  };

  let activeKind = '';
  let activeUrl = '';
  let controller = null;
  let feedbackTimer = 0;

  const showFeedback = (message) => {
    if (!feedback || !message) return;
    window.clearTimeout(feedbackTimer);
    feedback.textContent = message;
    feedback.hidden = false;
    feedbackTimer = window.setTimeout(() => {
      feedback.hidden = true;
      feedback.textContent = '';
    }, 4200);
  };

  const setBusy = (message = 'Loading…') => {
    const state = document.createElement('div');
    state.className = 'profile-account-dialog-state';
    state.setAttribute('role', 'status');
    state.textContent = message;
    body.replaceChildren(state);
  };

  const setError = (message) => {
    const state = document.createElement('div');
    state.className = 'profile-account-dialog-state error';
    state.setAttribute('role', 'alert');
    state.textContent = message;
    body.replaceChildren(state);
  };

  const close = () => {
    controller?.abort();
    controller = null;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  };

  const open = () => {
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  };

  const parsePage = (html) => new DOMParser().parseFromString(html, 'text/html');

  const extractPanel = (documentCopy, kind) => {
    const selector = configurations[kind]?.selector;
    const source = selector ? documentCopy.querySelector(selector) : null;
    return source?.cloneNode(true) || null;
  };

  const normalizeSessionPanel = (panel) => {
    if (!panel) return;
    panel.querySelectorAll('.session-list article').forEach((row) => {
      const copyBlock = row.querySelector(':scope > div');
      const strong = copyBlock?.querySelector('strong');
      const span = copyBlock?.querySelector('span');
      if (!strong || !span || strong.textContent.trim() === 'This browser') return;
      const userAgent = strong.textContent.trim();
      strong.textContent = 'Other session';
      const device = document.createElement('small');
      device.className = 'profile-session-device';
      device.textContent = userAgent;
      copyBlock.insertBefore(device, span);
    });
  };

  const installPanel = (documentCopy, kind) => {
    const panel = extractPanel(documentCopy, kind);
    if (!panel) return false;
    if (kind === 'sessions') normalizeSessionPanel(panel);
    body.replaceChildren(panel);
    return true;
  };

  const responseUrl = (response) => {
    try { return new URL(response.url, window.location.href); }
    catch (_error) { return new URL(activeUrl || window.location.href, window.location.href); }
  };

  const navigateIfSignedOut = (response) => {
    const url = responseUrl(response);
    if (url.pathname !== '/login') return false;
    window.location.assign(url.href);
    return true;
  };

  const load = async (kind, url) => {
    const configuration = configurations[kind];
    if (!configuration) return;
    activeKind = kind;
    activeUrl = url;
    dialog.dataset.kind = kind;
    title.textContent = configuration.title;
    copy.textContent = configuration.copy;
    setBusy();
    open();

    controller?.abort();
    controller = new AbortController();
    try {
      const response = await fetch(url, {
        credentials: 'same-origin',
        cache: 'no-store',
        signal: controller.signal,
        headers: {'X-InfoMancer-Dialog': 'profile-account'},
      });
      if (navigateIfSignedOut(response)) return;
      const documentCopy = parsePage(await response.text());
      if (!installPanel(documentCopy, kind)) {
        throw new Error('The account panel was not returned.');
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        setError('This account panel could not be loaded. You can still open the full page and try again.');
      }
    } finally {
      controller = null;
    }
  };

  triggers.forEach((trigger) => {
    trigger.addEventListener('click', (event) => {
      const kind = trigger.dataset.profileAccountDialog;
      if (!configurations[kind]) return;
      event.preventDefault();
      load(kind, trigger.href);
    });
  });

  dialog.querySelectorAll('[data-profile-account-dialog-close]').forEach((button) => {
    button.addEventListener('click', close);
  });
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    close();
  });

  body.addEventListener('click', (event) => {
    const toggle = event.target.closest?.('[data-password-toggle]');
    if (!toggle) return;
    const input = body.querySelector(`#${CSS.escape(toggle.dataset.passwordToggle || '')}`);
    if (!input) return;
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    toggle.textContent = showing ? 'Show' : 'Hide';
    toggle.setAttribute('aria-pressed', String(!showing));
    toggle.setAttribute('aria-label', `${showing ? 'Show' : 'Hide'} ${input.getAttribute('autocomplete') === 'current-password' ? 'current password' : 'password'}`);
  });

  body.addEventListener('submit', async (event) => {
    const form = event.target.closest?.('form');
    if (!form || !activeKind) return;
    event.preventDefault();
    const submitter = event.submitter;
    const action = submitter?.formAction || form.action || activeUrl;
    const method = (submitter?.formMethod || form.method || 'post').toUpperCase();
    const buttons = [...form.querySelectorAll('button, input[type="submit"]')];
    buttons.forEach((button) => { button.disabled = true; });

    try {
      const response = await fetch(action, {
        method,
        credentials: 'same-origin',
        cache: 'no-store',
        body: method === 'GET' ? undefined : new FormData(form),
        headers: {'X-InfoMancer-Dialog': 'profile-account'},
      });
      if (navigateIfSignedOut(response)) return;
      const url = responseUrl(response);
      const html = await response.text();
      const documentCopy = parsePage(html);

      if (activeKind === 'password') {
        const failed = !response.ok || Boolean(documentCopy.querySelector('.settings-form .form-error'));
        if (failed) {
          if (!installPanel(documentCopy, activeKind)) {
            setError('The password could not be changed. Open the full Password page and try again.');
          }
          return;
        }
        close();
        showFeedback(url.searchParams.get('message') || 'Password changed.');
        return;
      }

      if (activeKind === 'sessions') {
        if (!installPanel(documentCopy, activeKind)) {
          setError('Sessions could not be refreshed. Open the full Sessions page and try again.');
          return;
        }
        showFeedback(url.searchParams.get('message') || 'Sessions updated.');
      }
    } catch (_error) {
      setError('That account action could not be completed. Check your connection and try again.');
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  });
})();
