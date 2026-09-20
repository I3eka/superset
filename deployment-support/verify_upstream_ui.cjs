const assert = require('node:assert/strict');
const { chromium } = require('../superset-frontend/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    const chartResponses = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (new URL(response.url()).pathname === '/api/v1/chart/data') {
        chartResponses.push(response.status());
      }
    });
    const base = process.env.SUPERSET_VERIFY_URL || 'http://10.48.140.17:8088';
    await page.goto(base + '/login/?next=%2Fwelcome%2F', { waitUntil: 'networkidle' });
    await page.getByRole('link', { name: 'Apache Superset' }).click();
    await page.getByLabel('Username:').waitFor();
    assert(page.url().includes('/login/'));
    await page.getByLabel('Username:').fill('admin');
    await page.getByLabel('Password:').fill(process.env.ADMIN_PASSWORD);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/welcome/', { timeout: 45000 });
    await page.waitForLoadState('networkidle');
    const started = Date.now();
    await page.goto(base + '/dashboard/42/', { waitUntil: 'networkidle', timeout: 60000 });
    await page.locator('[data-test="chart-container"]').first().waitFor({ timeout: 45000 });
    assert(!(await page.locator('body').innerText()).includes('Unexpected error'));
    assert.deepEqual(errors, []);
    assert(chartResponses.every(status => status === 200), JSON.stringify(chartResponses));
    console.log('BROWSER_OK', JSON.stringify({
      login: true,
      anonymousLogoRedirect: true,
      dashboard: 42,
      renderedCharts: await page.locator('[data-test="chart-container"]').count(),
      chartResponses,
      dashboardMs: Date.now() - started,
    }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
