(() => {
  const nav = document.querySelector('.settings-jump-nav');
  if (!nav) return;

  const links = [...nav.querySelectorAll('a[href^="#"]')];
  const sections = links.map((link) => {
    const id = link.getAttribute('href').slice(1);
    return {link, target: document.getElementById(id)};
  }).filter((item) => item.target);
  if (!sections.length) return;

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let animationFrame = 0;
  let activeId = '';

  const chromeOffset = () => {
    const topbar = document.querySelector('.topbar');
    const topbarHeight = topbar?.getBoundingClientRect().height || 0;
    const navHeight = nav.getBoundingClientRect().height;
    const navTopGap = window.innerWidth > 980 ? 12 : 8;
    const cardGap = 18;
    return topbarHeight + navTopGap + navHeight + cardGap;
  };

  const targetY = (target) => Math.max(
    0,
    Math.round(window.scrollY + target.getBoundingClientRect().top - chromeOffset()),
  );

  const easeInOutCubic = (progress) => (
    progress < .5
      ? 4 * progress * progress * progress
      : 1 - Math.pow(-2 * progress + 2, 3) / 2
  );

  const scrollToTarget = (target, animate = true) => {
    cancelAnimationFrame(animationFrame);
    const start = window.scrollY;
    const end = targetY(target);
    const distance = end - start;
    if (!animate || reducedMotion.matches || Math.abs(distance) < 3) {
      window.scrollTo(0, end);
      return;
    }

    /* Keep the same ease-in/ease-out shape, but give the motion a little more
       breathing room so longer System jumps feel intentional instead of hurried. */
    const duration = Math.min(850, Math.max(430, 430 + Math.abs(distance) * .14));
    const started = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - started) / duration);
      window.scrollTo(0, start + distance * easeInOutCubic(progress));
      if (progress < 1) animationFrame = requestAnimationFrame(step);
    };
    animationFrame = requestAnimationFrame(step);
  };

  const markActive = (id) => {
    if (id === activeId) return;
    activeId = id;
    sections.forEach(({link, target}) => {
      const active = target.id === id;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  };

  links.forEach((link) => {
    link.addEventListener('click', (event) => {
      const id = link.getAttribute('href').slice(1);
      const target = document.getElementById(id);
      if (!target) return;
      event.preventDefault();
      history.pushState(null, '', `#${encodeURIComponent(id)}`);
      markActive(id);
      scrollToTarget(target, true);
    });
  });

  let scrollTick = 0;
  const updateActiveFromScroll = () => {
    scrollTick = 0;
    const threshold = chromeOffset() + 12;
    let current = sections[0];
    for (const item of sections) {
      if (item.target.getBoundingClientRect().top <= threshold) current = item;
      else break;
    }
    markActive(current.target.id);
  };

  window.addEventListener('scroll', () => {
    if (scrollTick) return;
    scrollTick = requestAnimationFrame(updateActiveFromScroll);
  }, {passive: true});

  window.addEventListener('resize', () => requestAnimationFrame(updateActiveFromScroll), {passive: true});

  const applyHashPosition = () => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    const target = id ? document.getElementById(id) : null;
    if (target && nav.querySelector(`a[href="#${CSS.escape(id)}"]`)) {
      markActive(id);
      scrollToTarget(target, false);
    } else {
      updateActiveFromScroll();
    }
  };

  window.addEventListener('hashchange', applyHashPosition);
  requestAnimationFrame(() => requestAnimationFrame(applyHashPosition));
})();