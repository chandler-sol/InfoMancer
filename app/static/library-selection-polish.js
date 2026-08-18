(() => {
  const coverLibrary = document.getElementById('cover-library');
  const libraryTable = document.querySelector('.library-table');
  if (!coverLibrary && !libraryTable) return;

  const itemFor = (target) => target?.closest?.('.cover-card, .library-title-row');
  const choiceFor = (item) => item?.querySelector('.library-title-choice');
  const inspectedItem = () => document.querySelector('.cover-card.workspace-selected, .library-title-row.workspace-selected');
  const selectedChoices = () => [...document.querySelectorAll('.library-title-choice:checked')];
  const titleIdFor = (item) => item?.dataset?.workspaceTitleId || '';

  let closeQueued = false;
  const queueInspectorClose = () => {
    if (closeQueued) return;
    closeQueued = true;
    window.setTimeout(() => {
      closeQueued = false;
      const inspector = document.getElementById('workspace-inspector');
      if (!inspector || inspector.hidden || !document.body.classList.contains('workspace-inspector-open')) return;
      inspector.querySelector('.workspace-inspector-close')?.click();
    }, 0);
  };

  /* If Ctrl/Cmd or Shift begins from a title that is currently being inspected,
     promote that inspected title into the bulk selection first. This makes the
     familiar "click one, Ctrl-click another" gesture produce two selected items
     instead of leaving one merely inspected and one actually selected. */
  document.addEventListener('click', (event) => {
    if (!(event.ctrlKey || event.metaKey || event.shiftKey)) return;
    const targetItem = itemFor(event.target);
    if (!targetItem) return;
    if (event.target.closest('input, button, summary, details, form, select, textarea, .item-action-menu')) return;
    if (selectedChoices().length) return;

    const current = inspectedItem();
    if (!current) return;
    const currentChoice = choiceFor(current);
    if (!currentChoice || currentChoice.checked) return;

    /* When the modified click is on the inspected item itself, workspace.js will
       toggle it. Preselect only when another item is the target so we do not toggle
       the inspected title twice. */
    if (titleIdFor(current) === titleIdFor(targetItem)) return;
    currentChoice.checked = true;
    currentChoice.dispatchEvent(new Event('change', {bubbles: true}));
  }, true);

  /* Checkbox, Ctrl/Cmd and Shift selection is a bulk-workflow state. Once at least
     one item is selected, the Inspector gets out of the way instead of continuing
     to occupy the viewport beside a multi-selection. */
  document.addEventListener('change', (event) => {
    if (!event.target.matches('.library-title-choice')) return;
    if (selectedChoices().length) queueInspectorClose();
  });

  /* Keep the inspector closed when selection is restored from session state or a
     partial Library refresh while a bulk selection is already active. */
  document.addEventListener('infomancer:library-results-updated', () => {
    if (selectedChoices().length) queueInspectorClose();
  });
})();
