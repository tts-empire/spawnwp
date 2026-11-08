// SpawnWP web analytics — self-hosted Matomo, cookieless.
if (!window.__spawnwpAnalytics) {
window.__spawnwpAnalytics = true;
// Loaded as a first-party script so the site CSP does not need 'unsafe-inline'.
var _paq = window._paq = window._paq || [];
// Privacy: no cookies, no consent banner required.
_paq.push(['disableCookies']);
_paq.push(['enableLinkTracking']);
(function () {
  var u = "https://stats.presenzaweb.net/";
  _paq.push(['setTrackerUrl', u + 'matomo.php']);
  _paq.push(['setSiteId', '6']);
  var d = document, g = d.createElement('script'), s = d.getElementsByTagName('script')[0];
  g.async = true; g.src = u + 'matomo.js'; s.parentNode.insertBefore(g, s);
})();

// Qualified SEO funnel events. The source path is the event name so Matomo can
// compare which pages move readers towards setup and source code. Explicit
// data-seo-funnel attributes keep tracking stable if a destination URL changes;
// the href checks remain as a site-wide fallback.
(function () {
  var lastPath;
  function pageView() {
    var path = window.location.pathname;
    if (path === lastPath) return;
    lastPath = path;
    _paq.push(['setCustomUrl', window.location.origin + path]);
    _paq.push(['setDocumentTitle', document.title]);
    _paq.push(['trackPageView']);
  }
  pageView();
  document.addEventListener('spawnwp:page-changed', pageView);
  function track(category, action) {
    _paq.push(['trackEvent', category, action, lastPath]);
  }

  document.addEventListener('click', async function (event) {
    var copy = event.target.closest && event.target.closest('[data-copy-install-command]');
    if (copy) {
      var command = copy.getAttribute('data-copy-install-command');
      try {
        if (!command) return;
        await navigator.clipboard.writeText(command);
        document.dispatchEvent(new CustomEvent('spawnwp:command-copied'));
        copy.textContent = 'Installation command copied';
      } catch (_error) {
        copy.textContent = 'Copy unavailable; select the command above';
      }
      return;
    }
    var link = event.target.closest && event.target.closest('a[href]');
    if (!link) return;
    var destination;
    try { destination = new URL(link.getAttribute('href'), window.location.href); }
    catch (_error) { return; }
    var funnel = link.getAttribute('data-seo-funnel');
    var navigation = link.getAttribute('data-seo-navigation');
    if (funnel) track('SEO Funnel', funnel);
    else if (destination.origin === window.location.origin) {
      if (destination.pathname === '/docs/requirements/') track('SEO Funnel', 'visit_requirements');
      else if (destination.pathname === '/docs/installation/' && destination.pathname !== lastPath) track('SEO Funnel', 'visit_installation');
    } else if (destination.origin === 'https://github.com' && /^\/tts-empire\/spawnwp(?:\/|$)/.test(destination.pathname)) track('SEO Funnel', 'visit_github');
    if (navigation) track('SEO Navigation', navigation);
  }, true);

  document.addEventListener('spawnwp:command-copied', function () {
    track('SEO Funnel', 'copy_install_command');
  });
})();
}
