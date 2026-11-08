// Material emits document$ after both first render and instant navigation.
// The shared tracker suppresses the first duplicate and hash-only changes.
if (typeof document$ !== 'undefined' && !window.__spawnwpDocsAnalytics) {
  window.__spawnwpDocsAnalytics = true;
  document$.subscribe(function () {
    document.dispatchEvent(new CustomEvent('spawnwp:page-changed'));
  });
}
