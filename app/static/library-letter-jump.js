(() => {
  const toolbar = document.querySelector('.library-display-toolbar');
  const alphabet = toolbar?.querySelector('.alphabet');
  if (!toolbar || !alphabet || toolbar.querySelector('.letter-jump-menu')) return;

  const active = alphabet.querySelector('a.active');
  const currentLetter = active?.textContent?.trim() || 'All';

  const menu = document.createElement('details');
  menu.className = 'letter-jump-menu';

  const summary = document.createElement('summary');
  summary.setAttribute('aria-label', `Jump to titles beginning with ${currentLetter}`);

  const label = document.createElement('span');
  label.className = 'letter-jump-label';
  label.textContent = 'Jump to';

  const current = document.createElement('strong');
  current.className = 'letter-jump-current';
  current.textContent = currentLetter;

  summary.append(label, current);

  const panel = document.createElement('div');
  panel.className = 'letter-jump-panel';

  const heading = document.createElement('span');
  heading.className = 'letter-jump-heading';
  heading.textContent = 'Jump to title';

  alphabet.classList.add('letter-jump-grid');
  panel.append(heading, alphabet);
  menu.append(summary, panel);
  toolbar.prepend(menu);
  toolbar.classList.add('has-letter-jump');

  document.addEventListener('click', event => {
    if (menu.open && !menu.contains(event.target)) menu.removeAttribute('open');
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.removeAttribute('open');
      summary.focus();
    }
  });
})();
