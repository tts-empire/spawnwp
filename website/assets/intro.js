// Set the landing-page animation state before the stylesheet can paint.
// Keeping this in an external same-origin script preserves the site's CSP.
(function () {
  try {
    if (!sessionStorage.getItem('spawnwp-intro-played')) {
      sessionStorage.setItem('spawnwp-intro-played', '1');
      document.documentElement.classList.add('intro-animate');
    }
  } catch (_error) {
    // Private browsing or disabled storage should never block the page.
  }
}());
