// SpawnWP web analytics — self-hosted Matomo, cookieless.
// Loaded as a first-party script so the site CSP does not need 'unsafe-inline'.
var _paq = window._paq = window._paq || [];
// Privacy: no cookies, no consent banner required.
_paq.push(['disableCookies']);
_paq.push(['trackPageView']);
_paq.push(['enableLinkTracking']);
(function () {
  var u = "https://stats.presenzaweb.net/";
  _paq.push(['setTrackerUrl', u + 'matomo.php']);
  _paq.push(['setSiteId', '6']);
  var d = document, g = d.createElement('script'), s = d.getElementsByTagName('script')[0];
  g.async = true; g.src = u + 'matomo.js'; s.parentNode.insertBefore(g, s);
})();

// Qualified SEO funnel events. The current path is the event name so Matomo can
// compare which landing pages move readers towards setup and source code.
(function () {
  function track(action) {
    _paq.push(['trackEvent', 'SEO Funnel', action, window.location.pathname]);
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href]');
    if (!link) return;
    var href = link.getAttribute('href') || '';
    if (href.indexOf('/docs/requirements/') === 0) track('visit_requirements');
    else if (href.indexOf('/docs/installation/') === 0) track('visit_installation');
    else if (href.indexOf('https://github.com/tts-empire/spawnwp') === 0) track('visit_github');
  });

  document.addEventListener('spawnwp:command-copied', function () {
    track('copy_install_command');
  });
})();
