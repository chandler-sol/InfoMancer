(() => {
  const widget = document.getElementById('task-widget');
  const popover = document.getElementById('task-popover');
  const toggle = document.getElementById('task-widget-toggle');
  if (!widget || !popover || !toggle) return;

  const preserveOpenVisibility = () => {
    if (
      !popover.hidden
      && toggle.getAttribute('aria-expanded') === 'true'
      && !widget.classList.contains('visible')
    ) {
      widget.classList.add('visible');
    }
  };

  // Clearing a completed/failed notification can make the canonical task renderer
  // temporarily decide that the widget has no content. Preserve visibility only for
  // that explicit user action. Do not observe the widget's class/hidden attributes:
  // rewriting an observed class attribute can create a self-sustaining mutation loop
  // that starves the WebView event loop when the task center is opened.
  widget.addEventListener('click', (event) => {
    const action = event.target.closest?.('.task-inline-action');
    if (!action || action.textContent.trim() !== 'Clear') return;
    queueMicrotask(preserveOpenVisibility);
    requestAnimationFrame(preserveOpenVisibility);
  });
})();
