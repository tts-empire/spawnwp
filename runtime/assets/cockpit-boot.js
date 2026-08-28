// Prevent the browser from painting the unstyled Cockpit shell while its
// authenticated stylesheet is loading. This runs before the stylesheet link.
(function () {
  const root = document.documentElement;
  root.classList.add('cockpit-preload');
  const reveal = () => root.classList.remove('cockpit-preload');
  const watch = () => {
    const css = document.getElementById('cockpit-css');
    if (!css) { reveal(); return; }
    css.addEventListener('load', reveal, { once: true });
    css.addEventListener('error', reveal, { once: true });
    if (css.sheet) reveal();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch, { once: true });
  else watch();
  // Never leave a page hidden if an intermediary drops the asset event.
  window.setTimeout(reveal, 1500);
}());
