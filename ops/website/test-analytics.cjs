// Run with Playwright installed and a built site served at SPAWNWP_PREVIEW_URL.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const preview = process.env.SPAWNWP_PREVIEW_URL || 'http://127.0.0.1:8877';
const base = 'https://spawnwp.com';
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext();
    // Keep the canonical origin so Material's sitemap-based instant loader is
    // exercised, while serving every page and asset from the isolated preview.
    await context.route('https://spawnwp.com/**', async route => {
      const url = new URL(route.request().url());
      const response = await route.fetch({ url: preview + url.pathname + url.search });
      await route.fulfill({ response });
    });
    // Keep every test pageview/event out of real analytics.
    await context.route('https://stats.presenzaweb.net/**', route => route.fulfill({ body: '', contentType: 'application/javascript' }));
    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    page.setDefaultNavigationTimeout(20000);
    const views = () => page.evaluate(() => window._paq.filter(x => x[0] === 'trackPageView').length);
    const events = action => page.evaluate(action => window._paq.filter(x => x[0] === 'trackEvent' && x[2] === action).length, action);
    await page.goto(base + '/docs/requirements/');
    await page.waitForFunction(() => window.__spawnwpAnalytics && window.__spawnwpDocsAnalytics);
    assert.equal(await views(), 1);
    await page.getByRole('link', { name: 'Installation', exact: true }).first().click();
    await page.waitForURL('**/docs/installation/');
    await page.waitForFunction(() => window._paq.filter(x => x[0] === 'trackPageView').length === 2);
    assert.equal(await events('visit_installation'), 1);
    assert.equal(await page.evaluate(() => window._paq.find(x => x[0] === 'trackEvent' && x[2] === 'visit_installation')[3]), '/docs/requirements/');
    await page.evaluate(() => document.dispatchEvent(new CustomEvent('spawnwp:page-changed')));
    assert.equal(await views(), 2, 'duplicate lifecycle must not create another pageview');
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => {} } });
    });
    await page.locator('[data-copy-install-command]').click();
    await page.waitForFunction(() => window._paq.some(x => x[0] === 'trackEvent' && x[2] === 'copy_install_command'));
    assert.equal(await events('copy_install_command'), 1);
    await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error('denied'); }; });
    await page.locator('[data-copy-install-command]').click();
    await page.waitForFunction(() => document.querySelector('[data-copy-install-command]').textContent.startsWith('Copy unavailable'));
    assert.equal(await events('copy_install_command'), 1, 'failed clipboard write is not a conversion');
    await page.goBack();
    await page.waitForURL('**/docs/requirements/');
    await page.waitForFunction(() => window._paq.filter(x => x[0] === 'trackPageView').length === 3);
    await page.goto(base + '/');
    await page.waitForFunction(() => window.__spawnwpAnalytics);
    await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => {} } }); });
    await page.locator('[data-copy-command]').first().click();
    await page.waitForFunction(() => window._paq.some(x => x[0] === 'trackEvent' && x[2] === 'copy_install_command'));
    assert.equal(await events('copy_install_command'), 1, 'homepage copy remains compatible');
    await page.setViewportSize({ width: 390, height: 844 });
    for (const path of ['/wordpress-sandbox/', '/alternatives/instawp/', '/guides/test-wordpress-multiple-php-versions/']) {
      await page.goto(base + path);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'mobile overflow: ' + path);
    }
    console.log('Browser checks passed: relative links, instant navigation/back, deduplication, successful/failed clipboard, homepage copy, mobile layouts.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
