(() => {
  const menu = document.querySelector('.more-filters-menu');
  if (!menu) return;

  document.addEventListener('click', (event) => {
    if (menu.open && !menu.contains(event.target)) menu.removeAttribute('open');
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !menu.open) return;
    menu.removeAttribute('open');
    menu.querySelector('summary')?.focus();
  });
})();
