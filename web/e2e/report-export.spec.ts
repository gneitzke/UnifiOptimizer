import { test, expect, type Route } from '@playwright/test';

/**
 * Report export action (docs/ARCHITECTURE.md §19; docs/REPORT_SPEC.md §Delivery).
 * Driven entirely against a mocked backend via page.route — no daemon, no
 * controller, no auth-setup. This guards the FRONTEND export contract that this
 * agent owns:
 *   - the dashboard header carries an "Export report" button,
 *   - the sidebar carries a "Report" destination pointing at /report,
 *   - the app chrome (sidebar + top bar) drops out of the print output, so the
 *     report prints as a standalone document rather than a screenshot of the
 *     dashboard shell.
 * The /report page content and its GET /api/report data are covered by the
 * report-page agent's own tests; here we only assert the entry points and the
 * print-chrome behaviour.
 */

const BASE = 'http://localhost:5173';

async function mockConfiguredBackend(page: import('@playwright/test').Page) {
  // A configured daemon: TokenGate reads this once and renders the app directly
  // (§18.1 "already set up = just works"), so no token wall stands in the way.
  await page.route('**/api/setup/status', (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ configured: true, controller_connected: true }),
    }),
  );
  // Every other read fails fast (503) so the dashboard resolves to its honest
  // "unavailable" states rather than parsing partial data. The predicate excludes
  // the status URL above, so the two routes never overlap. The export entry
  // points (button + sidebar) render regardless of whether any data loaded.
  await page.route(
    (url) => url.pathname.startsWith('/api/') && url.pathname !== '/api/setup/status',
    (route: Route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }),
  );
}

test.describe('Report export action', () => {
  test('dashboard header shows an Export report button', async ({ page }) => {
    await mockConfiguredBackend(page);
    await page.goto(BASE);
    await expect(page.getByRole('button', { name: 'Export report' })).toBeVisible();
  });

  test('sidebar carries a Report destination pointing at /report', async ({ page }) => {
    await mockConfiguredBackend(page);
    await page.goto(BASE);
    const link = page.locator('a[href="/report"]');
    await expect(link).toBeVisible();
    await expect(link).toContainText('Report');
  });

  test('app chrome is hidden in the print output', async ({ page }) => {
    await mockConfiguredBackend(page);
    await page.goto(BASE);

    // On screen, the sidebar destination and the top bar are visible.
    const sidebarLink = page.locator('a[href="/report"]');
    await expect(sidebarLink).toBeVisible();

    // Under print media, everything marked .no-print (the sidebar and the top
    // bar) collapses to display:none, leaving the report body as a standalone
    // document.
    await page.emulateMedia({ media: 'print' });
    await expect(sidebarLink).toBeHidden();
    await expect(page.getByRole('button', { name: 'Open command palette' })).toBeHidden();
  });
});
