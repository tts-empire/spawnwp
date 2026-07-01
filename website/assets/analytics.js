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
