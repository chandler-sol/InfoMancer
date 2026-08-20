(() => {
  const control = document.getElementById('cover-size-control');
  const coverLibrary = document.getElementById('cover-library');
  if (!control || !coverLibrary) return;

  const DESKTOP_KEY = 'infomancer-cover-density-desktop';
  const MOBILE_KEY = 'infomancer-cover-density-mobile';
  const LEGACY_KEY = 'infomancer-cover-size';
  const phoneQuery = window.matchMedia('(max-width: 600px)');

  /* The desktop control expresses visual density, never an exact column count.
     Auto-fill remains responsible for making full use of narrow, wide, and
     ultrawide viewports. Pixel values are deliberately an internal mapping only. */
  const desktopSteps = [
    {name: 'Compact', size: 120},
    {name: 'Dense', size: 165},
    {name: 'Balanced', size: 210},
    {name: 'Roomy', size: 255},
    {name: 'Spacious', size: 300},
  ];

  const mobileSteps = [
    {name: 'compact', label: 'Compact density, three covers across', columns: 3},
    {name: 'balanced', label: 'Balanced density, two covers across', columns: 2},
    {name: 'spacious', label: 'Spacious density, one large cover', columns: 1},
  ];

  const clampDesktop = value => Math.max(1, Math.min(desktopSteps.length, Number(value) || 3));

  const legacyDesktopStep = () => {
    const legacy = Number(localStorage.getItem(LEGACY_KEY));
    if (!Number.isFinite(legacy)) return 3;
    let nearest = 0;
    desktopSteps.forEach((step, index) => {
      if (Math.abs(step.size - legacy) < Math.abs(desktopSteps[nearest].size - legacy)) nearest = index;
    });
    return nearest + 1;
  };

  let desktopValue = clampDesktop(localStorage.getItem(DESKTOP_KEY) || legacyDesktopStep());
  let mobileValue = localStorage.getItem(MOBILE_KEY) || 'balanced';
  if (!mobileSteps.some(step => step.name === mobileValue)) mobileValue = 'balanced';

  const label = document.createElement('span');
  label.className = 'library-density-label';
  label.textContent = 'Density';

  const desktop = document.createElement('div');
  desktop.className = 'library-density-desktop';

  const compactEdge = document.createElement('span');
  compactEdge.className = 'library-density-edge';
  compactEdge.textContent = 'Compact';

  const range = document.createElement('input');
  range.type = 'range';
  range.className = 'library-density-range';
  range.min = '1';
  range.max = String(desktopSteps.length);
  range.step = '1';
  range.value = String(desktopValue);
  range.setAttribute('aria-label', 'Cover density');

  const spaciousEdge = document.createElement('span');
  spaciousEdge.className = 'library-density-edge';
  spaciousEdge.textContent = 'Spacious';
  desktop.append(compactEdge, range, spaciousEdge);

  const mobile = document.createElement('div');
  mobile.className = 'library-density-mobile';
  mobile.setAttribute('role', 'group');
  mobile.setAttribute('aria-label', 'Cover density');

  const iconForColumns = (columns) => {
    const cells = Array.from({length: columns}, (_unused, index) => {
      const width = columns === 1 ? 14 : columns === 2 ? 7 : 4;
      const gap = columns === 1 ? 0 : columns === 2 ? 2 : 2;
      const x = columns === 1 ? 5 : 2 + index * (width + gap);
      return `<rect x="${x}" y="3" width="${width}" height="18" rx="1.5"></rect>`;
    }).join('');
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${cells}</svg>`;
  };

  const mobileButtons = mobileSteps.map((step) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.density = step.name;
    button.title = step.label;
    button.setAttribute('aria-label', step.label);
    button.innerHTML = iconForColumns(step.columns);
    mobile.append(button);
    return button;
  });

  control.replaceChildren(label, desktop, mobile);
  control.classList.add('library-density-ready');

  const applyDesktop = (value, persist = true) => {
    desktopValue = clampDesktop(value);
    const step = desktopSteps[desktopValue - 1];
    range.value = String(desktopValue);
    range.setAttribute('aria-valuetext', step.name);
    coverLibrary.style.setProperty('--cover-size', `${step.size}px`);
    if (persist) {
      localStorage.setItem(DESKTOP_KEY, String(desktopValue));
      /* Keep the legacy value synchronized for older builds without surfacing it
         in the UI. This makes branch switching a graceful downgrade. */
      localStorage.setItem(LEGACY_KEY, String(step.size));
    }
  };

  const applyMobile = (value, persist = true) => {
    mobileValue = mobileSteps.some(step => step.name === value) ? value : 'balanced';
    coverLibrary.dataset.mobileDensity = mobileValue;
    mobileButtons.forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.density === mobileValue));
    });
    if (persist) localStorage.setItem(MOBILE_KEY, mobileValue);
  };

  const applyForViewport = () => {
    if (phoneQuery.matches) applyMobile(mobileValue, false);
    else applyDesktop(desktopValue, false);
  };

  range.addEventListener('input', () => applyDesktop(range.value));
  mobileButtons.forEach(button => {
    button.addEventListener('click', () => applyMobile(button.dataset.density));
  });

  if (typeof phoneQuery.addEventListener === 'function') {
    phoneQuery.addEventListener('change', applyForViewport);
  } else {
    phoneQuery.addListener(applyForViewport);
  }

  applyDesktop(desktopValue, false);
  applyMobile(mobileValue, false);
  applyForViewport();
})();
