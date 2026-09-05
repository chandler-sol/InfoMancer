(() => {
  const control = document.getElementById('cover-size-control');
  const coverLibrary = document.getElementById('cover-library');
  const viewToolbar = document.querySelector('.library-view-toolbar');
  const catalogTabs = document.querySelector('.catalog-tabs');
  if (!control || !coverLibrary) return;

  /* View mode and density describe how the current Library scope is displayed,
     so keep them with All / Movies / TV Shows rather than with alphabet filtering.
     This is a presentation handoff only; library-surface-lazy.js remains the sole
     owner of List/Covers behavior. */
  if (viewToolbar && catalogTabs && viewToolbar.parentElement !== catalogTabs) {
    catalogTabs.append(viewToolbar);
  }

  /* Density is secondary to the List/Covers choice. Keep the view toggle as the
     right-most anchored control so revealing Density grows the toolbar to the left
     instead of moving the control the user just clicked. */
  const viewControls = viewToolbar?.querySelector('.library-view-controls');
  const viewToggle = viewControls?.querySelector('.library-view-toggle');
  if (viewControls && viewToggle && control.nextElementSibling !== viewToggle) {
    viewControls.insertBefore(control, viewToggle);
  }

  const DESKTOP_KEY = 'infomancer-cover-density-desktop';
  const MOBILE_KEY = 'infomancer-cover-density-mobile';
  const LEGACY_KEY = 'infomancer-cover-size';
  const phoneQuery = window.matchMedia('(max-width: 600px)');

  /* Desktop density remains semantic. The internal card footprint is only the
     mechanism used by auto-fill, never a promise about an exact column count. */
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
  desktop.setAttribute('role', 'group');
  desktop.setAttribute('aria-label', 'Cover density');

  const iconForDesktopStep = (stepNumber) => {
    const count = desktopSteps.length - stepNumber + 1;
    const gap = count > 1 ? 1.25 : 0;
    const cardWidth = count === 1 ? 13 : Math.max(2.2, (18 - gap * (count - 1)) / count);
    const totalWidth = cardWidth * count + gap * (count - 1);
    const start = (24 - totalWidth) / 2;
    const cards = Array.from({length: count}, (_unused, index) => {
      const x = start + index * (cardWidth + gap);
      return `<rect x="${x.toFixed(2)}" y="4" width="${cardWidth.toFixed(2)}" height="16" rx="1"></rect>`;
    }).join('');
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${cards}</svg>`;
  };

  const desktopButtons = desktopSteps.map((step, index) => {
    const value = index + 1;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'library-density-step';
    button.dataset.densityStep = String(value);
    button.title = `${step.name} cover density`;
    button.setAttribute('aria-label', `${step.name} cover density`);
    button.innerHTML = iconForDesktopStep(value);
    desktop.append(button);
    return button;
  });

  const mobile = document.createElement('div');
  mobile.className = 'library-density-mobile';
  mobile.setAttribute('role', 'group');
  mobile.setAttribute('aria-label', 'Cover density');

  const iconForColumns = (columns) => {
    const cells = Array.from({length: columns}, (_unused, index) => {
      const width = columns === 1 ? 14 : columns === 2 ? 7 : 4;
      const gap = columns === 1 ? 0 : 2;
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

  const initiallyVisible = !control.hidden;
  control.replaceChildren(label, desktop, mobile);
  control.classList.add('library-density-ready');
  control.classList.toggle('is-collapsed', !initiallyVisible);
  control.setAttribute('aria-hidden', String(!initiallyVisible));
  control.inert = !initiallyVisible;
  control.hidden = false;

  const applyDesktop = (value, persist = true) => {
    desktopValue = clampDesktop(value);
    const step = desktopSteps[desktopValue - 1];
    coverLibrary.style.setProperty('--cover-size', `${step.size}px`);
    coverLibrary.dataset.desktopDensity = step.name.toLowerCase();
    desktopButtons.forEach((button) => {
      button.setAttribute('aria-pressed', String(Number(button.dataset.densityStep) === desktopValue));
    });
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

  desktopButtons.forEach(button => {
    button.addEventListener('click', () => applyDesktop(button.dataset.densityStep));
  });
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
