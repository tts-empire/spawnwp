(() => {
  const fallbackCopy = (text) => {
    const input = document.createElement('textarea');
    input.value = text;
    input.setAttribute('readonly', '');
    input.style.position = 'fixed';
    input.style.opacity = '0';
    document.body.appendChild(input);
    input.select();
    const copied = document.execCommand('copy');
    input.remove();
    if (!copied) throw new Error('Copy is not available');
  };

  document.querySelectorAll('[data-copy-command]').forEach((button) => {
    const original = button.innerHTML;
    button.addEventListener('click', async () => {
      const command = button.closest('.terminal')?.querySelector('code')?.textContent.trim();
      if (!command) return;
      try {
        if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(command);
        else fallbackCopy(command);
        button.classList.add('is-copied');
        button.setAttribute('aria-label', 'Installation command copied');
        button.setAttribute('title', 'Copied');
        button.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"></path></svg>';
        window.setTimeout(() => {
          button.classList.remove('is-copied');
          button.setAttribute('aria-label', 'Copy installation command');
          button.setAttribute('title', 'Copy command');
          button.innerHTML = original;
        }, 1600);
      } catch (_error) {
        button.setAttribute('aria-label', 'Unable to copy; select the command manually');
        button.setAttribute('title', 'Unable to copy');
      }
    });
  });

  const slider = document.querySelector('[data-slider]');
  if (!slider) return;

  const slides = [...slider.querySelectorAll('[data-slide]')];
  const dots = [...slider.querySelectorAll('[data-dot]')];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let current = 0;
  let timer;
  let touchStart = null;

  const show = (next) => {
    current = (next + slides.length) % slides.length;
    slides.forEach((slide, index) => {
      const active = index === current;
      slide.hidden = !active;
      slide.classList.toggle('is-active', active);
    });
    dots.forEach((dot, index) => {
      const active = index === current;
      dot.classList.toggle('is-active', active);
      if (active) dot.setAttribute('aria-current', 'true');
      else dot.removeAttribute('aria-current');
    });
  };

  const stop = () => window.clearInterval(timer);
  const start = () => {
    stop();
    if (!reducedMotion) timer = window.setInterval(() => show(current + 1), 6000);
  };

  slider.querySelector('[data-prev]').addEventListener('click', () => { show(current - 1); start(); });
  slider.querySelector('[data-next]').addEventListener('click', () => { show(current + 1); start(); });
  dots.forEach((dot, index) => dot.addEventListener('click', () => { show(index); start(); }));
  slider.addEventListener('mouseenter', stop);
  slider.addEventListener('mouseleave', start);
  slider.addEventListener('focusin', stop);
  slider.addEventListener('focusout', start);
  slider.addEventListener('touchstart', (event) => { touchStart = event.changedTouches[0].clientX; stop(); }, { passive: true });
  slider.addEventListener('touchend', (event) => {
    if (touchStart !== null) {
      const distance = event.changedTouches[0].clientX - touchStart;
      if (Math.abs(distance) > 45) show(current + (distance < 0 ? 1 : -1));
    }
    touchStart = null;
    start();
  }, { passive: true });

  start();
})();

// Entrance animations (hero spawn dots, support fab) play once per browser
// session instead of on every navigation, which read as flickering.
(() => {
  try {
    if (!sessionStorage.getItem('spawnwp-intro-played')) {
      sessionStorage.setItem('spawnwp-intro-played', '1');
      document.documentElement.classList.add('intro-animate');
    }
  } catch (_error) { /* storage unavailable: skip the animation, never break */ }
})();

// Mobile navigation: the header collapses the primary nav behind a hamburger
// (<=800px). Toggle it, keep aria-expanded in sync, and close on link tap,
// outside click or Escape. Runs on every page that has the header.
(() => {
  const header = document.querySelector('.site-header');
  const toggle = header?.querySelector('.nav-toggle');
  const nav = header?.querySelector('nav');
  if (!header || !toggle || !nav) return;

  const setOpen = (open) => {
    header.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  };

  toggle.addEventListener('click', () => setOpen(!header.classList.contains('nav-open')));
  nav.addEventListener('click', (event) => { if (event.target.closest('a')) setOpen(false); });
  document.addEventListener('click', (event) => {
    if (header.classList.contains('nav-open') && !header.contains(event.target)) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && header.classList.contains('nav-open')) { setOpen(false); toggle.focus(); }
  });
})();
