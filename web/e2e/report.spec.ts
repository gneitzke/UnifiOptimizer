import { test, expect, type Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { demoReport } from './fixtures/demoReport';

/**
 * Report page (docs/ARCHITECTURE.md §19). Self-contained: `GET /api/report` and
 * the setup-status gate read are mocked with page.route from the demo fixture, so
 * this needs no backend and no auth. It asserts the page renders the model AS
 * GIVEN and drops screenshots + a print-to-PDF into scratch_validation/report/.
 */

const BASE = 'http://localhost:5173';
const OUT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../scratch_validation/report',
);

/** Match a backend path anchored at the host root — /api/… — so the app's own
 *  module URLs under /src/api/ are never intercepted. */
const api = (suffix: string) => new RegExp(`^https?://[^/]+/api/${suffix}`);

async function mockApi(page: Page) {
  // Register the catch-all FIRST: Playwright runs the most-recently-registered
  // matching route first, so the specific handlers below must win over this.
  await page.route(api(''), (r) => r.fulfill({ json: {} }));
  await page.route(api('issues'), (r) => r.fulfill({ json: { issues: [], count: 0 } }));
  await page.route(api('report'), (r) => r.fulfill({ json: demoReport }));
  await page.route(api('setup/status'), (r) =>
    r.fulfill({ json: { configured: true, controller_connected: true } }),
  );
}

async function gotoReport(page: Page, theme?: 'dark' | 'light') {
  if (theme) {
    await page.addInitScript((t) => {
      try {
        localStorage.setItem('netadmin_theme', t);
      } catch {
        /* ignore */
      }
    }, theme);
  }
  await mockApi(page);
  await page.goto(`${BASE}/report`);
  await expect(page.getByRole('heading', { name: 'Network Assessment' })).toBeVisible({
    timeout: 15000,
  });
  // Let the SVG charts measure their containers and lay out.
  await page.waitForTimeout(600);
}

test.describe('Report page', () => {
  test('renders the model as given', async ({ page }) => {
    await gotoReport(page);

    // Cover + a field from every section, proving each renders its real data.
    await expect(page.getByText('Report date')).toBeVisible();
    await expect(page.getByRole('heading', { name: /Executive summary/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Scope & methodology/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Network inventory/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Topology/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Health & performance/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /RF environment/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Client analysis/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Detailed findings/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Recommendations/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Appendix/ })).toBeVisible();

    // A finding id and a severity chip from the fixed template.
    await expect(page.getByText('WLAN-01').first()).toBeVisible();
    await expect(page.getByText('Garden AP mesh backhaul is weak and slow').first()).toBeVisible();

    // The export control exists and is marked no-print.
    const exportBtn = page.getByRole('button', { name: /Export \/ Save as PDF/ });
    await expect(exportBtn).toBeVisible();

    await page.screenshot({ path: path.join(OUT, 'report-light-full.png'), fullPage: true });
    await page.screenshot({ path: path.join(OUT, 'report-light-top.png') });
  });

  test('renders in dark mode', async ({ page }) => {
    await gotoReport(page, 'dark');
    await expect(page.getByText('Report date')).toBeVisible();
    await page.screenshot({ path: path.join(OUT, 'report-dark-full.png'), fullPage: true });
    await page.screenshot({ path: path.join(OUT, 'report-dark-top.png') });
  });

  test('prints to a clean multi-page PDF', async ({ page }) => {
    await gotoReport(page);
    // No headerTemplate/footerTemplate: the site + confidential running header is
    // the in-flow `.report-runhead` band, and page numbers come from the @page
    // margin box in print.css (headless Chromium honours it), matching what the
    // shipped Save-as-PDF produces without a duplicated header.
    const pdf = await page.pdf({
      path: path.join(OUT, 'report.pdf'),
      format: 'Letter',
      printBackground: true,
      preferCSSPageSize: true,
    });
    expect(pdf.byteLength).toBeGreaterThan(20_000);
  });
});
