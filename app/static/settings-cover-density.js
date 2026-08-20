(() => {
  const range = document.getElementById('settings-cover-size');
  const output = document.getElementById('settings-cover-size-output');
  if (!range || !output) return;

  const steps = [
    {name: 'Compact', size: 120},
    {name: 'Dense', size: 165},
    {name: 'Balanced', size: 210},
    {name: 'Roomy', size: 255},
    {name: 'Spacious', size: 300},
  ];

  const original = Number(range.value) || 180;
  let nearest = 0;
  steps.forEach((step, index) => {
    if (Math.abs(step.size - original) < Math.abs(steps[nearest].size - original)) nearest = index;
  });

  const fieldName = range.getAttribute('name') || 'default_cover_size';
  range.removeAttribute('name');
  range.min = '1';
  range.max = String(steps.length);
  range.step = '1';
  range.value = String(nearest + 1);
  range.setAttribute('aria-label', 'Default cover density');

  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.name = fieldName;
  range.after(hidden);

  const label = range.closest('label');
  if (label) {
    [...label.childNodes].forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE && node.textContent.includes('Default cover size')) {
        node.textContent = node.textContent.replace('Default cover size', 'Default cover density');
      }
    });
  }

  const sync = () => {
    const index = Math.max(0, Math.min(steps.length - 1, Number(range.value) - 1));
    const step = steps[index];
    hidden.value = String(step.size);
    output.value = step.name;
    output.textContent = step.name;
    range.setAttribute('aria-valuetext', step.name);
  };

  range.addEventListener('input', sync);
  sync();
})();
