// SpawnWP web analytics — self-hosted Matomo, cookieless.
(() => {
  'use strict';
  if (window.spawnwpAnalyticsLoaded) return;
  window.spawnwpAnalyticsLoaded = true;
  window._paq = window._paq || [];
  // Matomo replaces _paq after loading; always dispatch to its current queue.
  const queue = { push: (command) => window._paq.push(command) };
  queue.push(['disableCookies']);
  queue.push(['setTrackerUrl', 'https://stats.presenzaweb.net/matomo.php']);
  queue.push(['setSiteId', '6']);
  queue.push(['enableLinkTracking']);

  const track = (category, action, path = window.location.pathname) => {
    queue.push(['trackEvent', category, action, path]);
  };
  let previousUrl;
  function pageView() {
    const url = new URL(window.location.href);
    url.hash = '';
    if (url.href === previousUrl) return;
    if (previousUrl) queue.push(['setReferrerUrl', previousUrl]);
    queue.push(['setCustomUrl', url.href]);
    queue.push(['setDocumentTitle', document.title]);
    queue.push(['trackPageView']);
    previousUrl = url.href;
  }
  // In the docs this script loads after Material's bundle. Its observable also
  // emits for the initial document, so this is the sole pageview entry point.
  if (window.document$) window.document$.subscribe(pageView);
  else pageView();

  document.addEventListener('click', (event) => {
    const link = event.target.closest && event.target.closest('a[href]');
    if (!link) return;
    const url = new URL(link.getAttribute('href'), window.location.href);
    const local = url.origin === window.location.origin;
    let funnel = link.getAttribute('data-seo-funnel');
    if (!funnel && local) {
      if (url.pathname === '/' && url.hash === '#install') funnel = 'open_install_section';
      else if (url.pathname === '/docs/requirements/') funnel = 'visit_requirements';
      else if (url.pathname === '/docs/installation/') funnel = 'visit_installation';
    }
    if (!funnel && url.origin === 'https://github.com' &&
        /^\/tts-empire\/spawnwp(?:\/|$)/.test(url.pathname)) funnel = 'visit_github';
    if (funnel) track('SEO Funnel', funnel);
    const navigation = link.getAttribute('data-seo-navigation');
    if (navigation) track('SEO Navigation', navigation);
  }, true);

  document.addEventListener('spawnwp:command-copied', (event) => {
    track('SEO Funnel', 'copy_install_command', event.detail?.path);
  });

  document.querySelectorAll('[data-demo-video]').forEach((video) => {
    const sent = new Set();
    const once = (action) => {
      if (sent.has(action)) return;
      sent.add(action);
      track('Demo Video', action);
    };
    const progress = () => {
      if (!Number.isFinite(video.duration) || video.duration <= 0) return;
      // played excludes seeking, and replaying a range does not add time.
      let played = 0;
      for (let i = 0; i < video.played.length; i += 1) {
        played += video.played.end(i) - video.played.start(i);
      }
      if (played >= video.duration / 2) once('demo_video_50');
    };
    video.addEventListener('playing', () => once('demo_video_start'));
    video.addEventListener('timeupdate', progress);
    video.addEventListener('ended', () => {
      progress();
      once('demo_video_complete');
    });
  });

  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://stats.presenzaweb.net/matomo.js';
  document.head.appendChild(script);
})();
