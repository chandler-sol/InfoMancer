(() => {
  const widget = document.getElementById('task-widget');
  const popover = document.getElementById('task-popover');
  const toggle = document.getElementById('task-widget-toggle');
  if (!widget || !popover || !toggle) return;

  const preserveOpenVisibility = () => {
    if (!popover.hidden && toggle.getAttribute('aria-expanded') === 'true') {
      widget.classList.add('visible');
    }
  };

  widget.addEventListener('click', (event) => {
    const action = event.target.closest?.('.task-inline-action');
    if (!action || action.textContent.trim() !== 'Clear') return;
    queueMicrotask(preserveOpenVisibility);
    requestAnimationFrame(preserveOpenVisibility);
  });

  new MutationObserver(preserveOpenVisibility).observe(widget, {
    attributes: true,
    attributeFilter: ['class'],
  });
  new MutationObserver(preserveOpenVisibility).observe(popover, {
    attributes: true,
    attributeFilter: ['hidden'],
  });
})();
