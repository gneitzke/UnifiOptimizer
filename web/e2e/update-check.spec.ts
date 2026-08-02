import { test, expect, type Page, type Route } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Settings → Software update (docs/ARCHITECTURE.md §23). Self-contained: every
 * `/api/*` call is mocked with page.route, so this needs no daemon, no
 * controller and no auth-setup — and crucially never touches PyPI or the live
 * network.
 *
 * What it guards is the honesty contract of the "Check now" control, which is
 * the whole reason the control exists:
 *
 *   1. an update is available   → the version line and the correct action for
 *      the install method (a real Update button only where the daemon can
 *      self-upgrade; instructions on a container install),
 *   2. up to date               → says so, and says when it last knew,
 *   3. the check itself failed  → `POST /system/update/check` answers 200 with
 *      the *cached* result when PyPI is unreachable, flagged by `checked: false`
 *      (and an `error` string) rather than left for the client to infer from a
 *      stalled `checked_ts` (Gitea #47). That case must read as unverified,
 *      never as "up to date".
 *
 * It also drops screenshots of all three, in both themes, into
 * scratch_validation/update-check/ (gitignored).
 */

const BASE = 'http://localhost:5173';
const OUT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../scratch_validation/update-check',
);

/** Match a backend path anchored at the host root — /api/… — so the app's own
 *  module URLs under /src/api/ are never intercepted. */
const api = (suffix: string) => new RegExp(`^https?://[^/]+/api/${suffix}`);

const NOW = Math.floor(Date.now() / 1000);
const FOUR_HOURS_AGO = NOW - 4 * 3600;

const health = {
  status: 'ok',
  ready: true,
  version: '0.7.1',
  uptime_s: 93_600,
  now: NOW,
  db: { path: '/data/netadmin.db', size_bytes: 48_234_496 },
  entities: { total: 41, by_type: { device: 9, client: 32 } },
  jobs: [
    { job: 'devices', interval_s: 300, last_success_age_s: 42, status: 'ok' },
    { job: 'clients', interval_s: 300, last_success_age_s: 61, status: 'ok' },
  ],
  websocket: { state: 'connected' },
  components: {},
  backfill: 'complete',
};

/** The container deployment the owner actually runs: an update exists, and
 *  pip-upgrading inside the container would write to a layer that vanishes on
 *  restart, so `self_upgrade_supported` is correctly false. */
const containerUpdateAvailable = {
  current_version: '0.7.1',
  latest_version: '0.7.2',
  update_available: true,
  install_method: 'container',
  variant: 'macmini',
  self_upgrade_supported: false,
  checked_ts: FOUR_HOURS_AGO,
  checked: true,
  error: null,
  skipped_version: null,
  snoozed_until: null,
  upgrade_state: null,
  release_url: 'https://pypi.org/project/unifioptimizer/0.7.2/',
};

const upToDate = {
  ...containerUpdateAvailable,
  latest_version: '0.7.1',
  update_available: false,
  release_url: 'https://pypi.org/project/unifioptimizer/0.7.1/',
};

const pipUpdateAvailable = {
  ...containerUpdateAvailable,
  install_method: 'pip',
  variant: null,
  self_upgrade_supported: true,
};

type UpdatePayload = typeof upToDate;

async function mockApi(
  page: Page,
  get: UpdatePayload,
  /** What `POST /system/update/check` answers. Defaults to a check that reached
   *  PyPI (`checked_ts` moves to now, `checked: true`); pass a body with
   *  `checked: false` (and an `error`) to simulate PyPI being unreachable,
   *  which is exactly what the endpoint does today. */
  post?: UpdatePayload,
) {
  // Catch-all first: Playwright runs the most-recently-registered match first,
  // so the specific handlers below win.
  await page.route(api(''), (r: Route) => r.fulfill({ json: {} }));
  await page.route(api('issues'), (r: Route) => r.fulfill({ json: { issues: [], count: 0 } }));
  await page.route(api('health'), (r: Route) => r.fulfill({ json: health }));
  await page.route(api('setup/status'), (r: Route) =>
    r.fulfill({ json: { configured: true, controller_connected: true } }),
  );
  await page.route(api('system/update'), (r: Route) => {
    if (r.request().method() === 'POST') {
      return r.fulfill({
        json: post ?? { ...get, checked_ts: Math.floor(Date.now() / 1000), checked: true, error: null },
      });
    }
    return r.fulfill({ json: get });
  });
}

/** The settings section itself. The update banner in the app shell says several
 *  of the same things, so every assertion is scoped here rather than to the page. */
function section(page: Page) {
  return page.getByRole('main').locator('section').filter({ hasText: 'Software update' });
}

async function gotoSettings(page: Page, theme: 'light' | 'dark') {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem('netadmin_theme', t);
      // The first-run tour otherwise takes over the page and walks the dashboard.
      localStorage.setItem('netadmin_tour_seen', '1');
    } catch {
      /* ignore */
    }
  }, theme);
  await page.goto(`${BASE}/settings`);
  // The top bar carries an <h1>Settings</h1> too; the page's own title is the h2.
  await expect(page.locator('h2', { hasText: 'Settings' })).toBeVisible({ timeout: 15000 });
}

test.describe('Settings → Software update', () => {
  test('an available update names both versions and offers only the action that works', async ({
    page,
  }) => {
    await mockApi(page, containerUpdateAvailable);
    await gotoSettings(page, 'light');
    const s = section(page);

    await expect(s.getByText('0.7.2 is available. You are on 0.7.1.')).toBeVisible();
    await expect(s.getByText('Checked 4h ago')).toBeVisible();
    // A container install cannot pip-upgrade itself: instructions, not a button
    // that would do nothing.
    await expect(s.getByRole('button', { name: 'How to update' })).toBeVisible();
    await expect(s.getByRole('button', { name: /^Update$/ })).toHaveCount(0);
    await expect(
      s.getByText('This install is updated on the host, not from this page.'),
    ).toBeVisible();

    // …and the instructions are the ones for THIS install.
    await s.getByRole('button', { name: 'How to update' }).click();
    await expect(page.getByText('./deploy/update-macmini.sh')).toBeVisible();
  });

  test('a self-upgradable pip install gets the real Update button', async ({ page }) => {
    await mockApi(page, pipUpdateAvailable);
    await gotoSettings(page, 'light');
    const s = section(page);
    await expect(s.getByRole('button', { name: /^Update$/ })).toBeVisible();
    await expect(s.getByRole('button', { name: 'How to update' })).toHaveCount(0);
    await expect(
      s.getByText('Installing backs up the database first and restarts the daemon.'),
    ).toBeVisible();
  });

  test('Check now reports a fresh, successful check', async ({ page }) => {
    await mockApi(page, upToDate);
    await gotoSettings(page, 'light');
    const s = section(page);

    await expect(s.getByText('You are on 0.7.1, the latest release.')).toBeVisible();
    await expect(s.getByText('Checked 4h ago')).toBeVisible();

    await s.getByRole('button', { name: 'Check now' }).click();
    await expect(s.getByText('Checked just now')).toBeVisible();
    await expect(s.getByText('You are on 0.7.1, the latest release.')).toBeVisible();
  });

  test('a check that never reached PyPI does not render as up to date', async ({ page }) => {
    // The real failure shape: 200, the stale cached body, `checked: false` and
    // an `error` naming why (Gitea #47) — never inferred from a stalled
    // `checked_ts` alone.
    await mockApi(page, upToDate, { ...upToDate, checked: false, error: 'connection refused' });
    await gotoSettings(page, 'light');
    const s = section(page);

    await s.getByRole('button', { name: 'Check now' }).click();

    await expect(
      s.getByText("Couldn't reach PyPI, so this answer is unverified. (connection refused)"),
    ).toBeVisible();
    await expect(s.getByText('You are on 0.7.1, the latest release.')).toHaveCount(0);
    await expect(s.getByText(/Showing the last completed check/)).toBeVisible();
    await expect(s.getByText(/0\.7\.1 was the latest release/)).toBeVisible();
  });

  test('a failed check keeps a known-available update visible, in the past tense', async ({
    page,
  }) => {
    await mockApi(page, containerUpdateAvailable, {
      ...containerUpdateAvailable,
      checked: false,
      error: 'timed out',
    });
    await gotoSettings(page, 'light');
    const s = section(page);

    await s.getByRole('button', { name: 'Check now' }).click();
    await expect(
      s.getByText("Couldn't reach PyPI, so this answer is unverified. (timed out)"),
    ).toBeVisible();
    await expect(
      s.getByText(/0\.7\.2 was available, and you are on 0\.7\.1\./),
    ).toBeVisible();
    // The action still points at the instructions for this install.
    await expect(s.getByRole('button', { name: 'How to update' })).toBeVisible();
  });

  test('a 401 says nothing was checked rather than swallowing the click', async ({ page }) => {
    await page.route(api(''), (r: Route) => r.fulfill({ json: {} }));
    await page.route(api('issues'), (r: Route) => r.fulfill({ json: { issues: [], count: 0 } }));
    await page.route(api('health'), (r: Route) => r.fulfill({ json: health }));
    await page.route(api('setup/status'), (r: Route) =>
      r.fulfill({ json: { configured: true, controller_connected: true } }),
    );
    await page.route(api('system/update'), (r: Route) => {
      if (r.request().method() === 'POST') {
        return r.fulfill({ status: 401, json: { detail: 'unauthorized' } });
      }
      return r.fulfill({ json: upToDate });
    });
    await gotoSettings(page, 'light');
    const s = section(page);

    await s.getByRole('button', { name: 'Check now' }).click();
    // The just-in-time prompt opens; dismissing it must not leave a lie on screen.
    await page.getByRole('button', { name: /Cancel/ }).first().click();
    await expect(s.getByText('Nothing was checked: the access token is required.')).toBeVisible();
  });

  test('never-checked is not up to date either', async ({ page }) => {
    await mockApi(page, { ...upToDate, latest_version: null, checked_ts: null });
    await gotoSettings(page, 'light');
    const s = section(page);
    await expect(s.getByText(/No check has completed yet/)).toBeVisible();
    await expect(s.getByText('Never checked.')).toBeVisible();
  });

  test('adds no horizontal scroll', async ({ page }) => {
    await mockApi(page, containerUpdateAvailable);

    // At tablet width the whole page fits, this section included.
    await page.setViewportSize({ width: 768, height: 900 });
    await gotoSettings(page, 'light');
    const atTablet = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(atTablet).toBe(0);

    // At 360px the settings page already overflows for reasons that predate this
    // section (the fixed four-column cadences grid, and a sidebar that does not
    // collapse). What this section owns is that it adds nothing to that: measured
    // with it shown and with it hidden, the page overflow is identical.
    await page.setViewportSize({ width: 360, height: 900 });
    const narrow = await page.evaluate(() => {
      const doc = document.documentElement;
      const mine = Array.from(document.querySelectorAll('main section')).find((el) =>
        el.textContent?.includes('Software update'),
      ) as HTMLElement;
      const withSection = doc.scrollWidth - doc.clientWidth;
      mine.style.display = 'none';
      const withoutSection = doc.scrollWidth - doc.clientWidth;
      mine.style.display = '';
      return { withSection, withoutSection };
    });
    expect(narrow.withSection).toBe(narrow.withoutSection);
  });
});

/* ---- Screenshots: three outcomes × two themes ---------------------------- */

for (const theme of ['light', 'dark'] as const) {
  test(`screenshots — ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 900 });

    const shots: Array<[string, () => Promise<void>]> = [
      [
        'available',
        async () => {
          await mockApi(page, containerUpdateAvailable);
          await gotoSettings(page, theme);
        },
      ],
      [
        'up-to-date',
        async () => {
          await mockApi(page, upToDate);
          await gotoSettings(page, theme);
          await section(page).getByRole('button', { name: 'Check now' }).click();
          await expect(section(page).getByText('Checked just now')).toBeVisible();
        },
      ],
      [
        'check-failed',
        async () => {
          await mockApi(page, upToDate, { ...upToDate, checked: false, error: 'connection refused' });
          await gotoSettings(page, theme);
          await section(page).getByRole('button', { name: 'Check now' }).click();
          await expect(
            section(page).getByText("Couldn't reach PyPI, so this answer is unverified."),
          ).toBeVisible();
        },
      ],
    ];

    for (const [name, arrange] of shots) {
      await arrange();
      if (name === 'available') {
        // The container case end to end: the button that IS offered leads to the
        // command that actually updates this install.
        await section(page).getByRole('button', { name: 'How to update' }).click();
        await expect(page.getByText('./deploy/update-macmini.sh')).toBeVisible();
        await page.screenshot({ path: path.join(OUT, `${theme}-howto.png`) });
        await page.getByRole('button', { name: 'Close' }).click();
      }

      // Prove the theme actually painted before capturing: an earlier attempt at
      // this shipped two dark files labelled light/dark.
      const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor.trim());
      expect(bg).toBe(theme === 'light' ? 'rgb(245, 245, 247)' : 'rgb(22, 22, 24)');

      await section(page).screenshot({ path: path.join(OUT, `${theme}-${name}.png`) });
      await page.screenshot({ path: path.join(OUT, `${theme}-${name}-page.png`) });
    }
  });
}
