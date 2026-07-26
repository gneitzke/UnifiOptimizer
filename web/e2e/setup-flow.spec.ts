import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * First-run onboarding flow (docs/ARCHITECTURE.md §18), driven entirely against a
 * mocked backend via page.route — no daemon, no controller, no auth-setup. Every
 * assertion here is about the FRONTEND contract: which surface renders for which
 * setup state, that the API key is transmitted but never echoed back to the UI,
 * that the token is shown once, and that failures surface clean copy (never a raw
 * status). The read-only / no-mutation guarantees live in the Python setup router
 * tests; this file guards the UI half of the contract.
 */

const BASE = 'http://localhost:5173';
const TOKEN_KEY = 'netadmin_api_token';
const UI_TOKEN = 'netadmin_tok_EXAMPLE_A1b2C3d4E5f6G7h8J9k0L1m2N3p4';

// Shapes mirror netadmin/server/routers/setup.py: detect returns
// {console, playbook, console_url} (no host echo); errors are {ok:false, code, error}.
const DETECT_OK = {
  console_url: 'https://192.168.1.1/',
  console: {
    kind: 'cloudkey_gen2_plus',
    model: 'UCK-G2-Plus',
    is_unifi_os: true,
    network_version: '9.1.120',
    api_key_supported: true,
    api_key_status: 'supported',
    recommended_auth: 'api_key',
    reachable: true,
    detail: null,
  },
  playbook: {
    label: 'UniFi CloudKey Gen2 Plus (UCK-G2-Plus)',
    auth_mode: 'api_key',
    supports_api_key: true,
    api_key_status: 'supported',
    steps: [
      'Browse to https://<cloudkey-ip> and open the UniFi Network application.',
      'In the Network application, open Settings -> Control Plane -> Integrations.',
      'Click Create API Key, name it, and copy the key (shown only once).',
    ],
  },
};

const DETECT_UNREACHABLE = {
  console_url: 'https://10.255.255.1/',
  console: {
    kind: 'unreachable',
    model: null,
    is_unifi_os: false,
    network_version: null,
    api_key_supported: false,
    api_key_status: 'unreachable',
    recommended_auth: 'none',
    reachable: false,
    detail: 'no UniFi console answered the read-only probe',
  },
  playbook: { label: 'No UniFi console detected', auth_mode: 'none', steps: [] },
};

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

// Origin-anchored matcher: hits the real `/api/...` calls but NOT the Vite dev
// server's own `/src/api/*.ts` module scripts (a plain `**/api/**` glob would
// swallow those and the app would never boot).
const api = (suffix: string) => new RegExp(`^https?://[^/]+/api/${suffix}`);

/**
 * App-shell mocks so the dashboard renders after entering it. Order matters:
 * Playwright's last-registered route wins, so the benign catch-all is registered
 * FIRST and the specific shapes (health, issues, sle) override it — otherwise the
 * catch-all's `{}` would reach the shell hooks and crash them (`issues`/`sles`
 * not iterable). A test's own routes register later still and win over these.
 */
async function mockAppApis(page: Page) {
  await page.route(api(''), (r) => json(r, 200, {}));
  await page.route(api('health$'), (r) =>
    json(r, 200, {
      status: 'ok',
      ready: true,
      uptime_s: 1,
      now: Date.now() / 1000,
      db: { path: 'demo.db', size_bytes: 1 },
      entities: { total: 0, by_type: {} },
      jobs: [],
      websocket: { state: 'connected' },
      components: {},
      backfill: 'idle',
    }),
  );
  await page.route(api('issues'), (r) => json(r, 200, { issues: [], count: 0 }));
  await page.route(api('sle'), (r) => json(r, 200, { sles: {}, headline: null }));
}

async function freshLoad(page: Page) {
  // Clear the stored token AND mark the guided tour seen: the first-run tour's
  // overlay otherwise intercepts clicks, so nav/actions wouldn't fire in-app.
  await page.addInitScript((k) => {
    try {
      localStorage.removeItem(k);
      localStorage.setItem('netadmin_tour_seen', '1');
    } catch {
      /* ignore */
    }
  }, TOKEN_KEY);
}

// ---------------------------------------------------------------------------
// 1. Fresh install → SetupFlow, full happy path
// ---------------------------------------------------------------------------

test.describe('Fresh install onboarding', () => {
  test.beforeEach(async ({ page }) => {
    await freshLoad(page);
    await mockAppApis(page);
    await page.route(api('setup/status$'), (r) =>
      json(r, 200, { configured: false, controller_connected: false }),
    );
  });

  test('detect → connect → token shown once → enter dashboard', async ({ page }) => {
    let connectBody: Record<string, unknown> | null = null;

    await page.route(api('setup/detect$'), (r) => json(r, 200, DETECT_OK));
    await page.route(api('setup/connect$'), (r) => {
      connectBody = JSON.parse(r.request().postData() || '{}');
      return json(r, 200, { ok: true, ui_token: UI_TOKEN });
    });

    await page.goto(BASE);

    // Step 1: the setup flow, not the returning-user gate.
    await expect(page.getByRole('heading', { name: 'Connect your network' })).toBeVisible();

    await page.getByLabel('Controller address').fill('https://192.168.1.1');
    await page.getByRole('button', { name: 'Detect' }).click();

    // Detection result: honest "Found:" line, the playbook steps, and the link.
    await expect(page.getByText(/Found:.*CloudKey Gen2 Plus/)).toBeVisible();
    await expect(
      page.getByText('Settings -> Control Plane -> Integrations', { exact: false }),
    ).toBeVisible();
    const openLink = page.getByRole('link', { name: /Open my controller/ });
    await expect(openLink).toHaveAttribute('target', '_blank');
    await expect(openLink).toHaveAttribute('rel', /noopener/);

    // Paste the key and connect.
    await page.getByLabel('Paste your API key', { exact: true }).fill('unifi-key-XYZ');
    await page.getByRole('button', { name: 'Connect' }).click();

    // Step 2: the token, shown once, large.
    await expect(page.getByRole('heading', { name: "You're connected" })).toBeVisible();
    await expect(page.getByText(UI_TOKEN)).toBeVisible();

    // The key was transmitted to the daemon, and the token is NOT the key.
    expect(connectBody).toMatchObject({ host: 'https://192.168.1.1', api_key: 'unifi-key-XYZ' });
    expect(UI_TOKEN).not.toContain('unifi-key-XYZ');

    // Enter the dashboard: token persisted, setup UI gone. (§18.1 step 2 is a
    // "collecting now" confirmation, not a wall — the CTA goes straight in.)
    await page.getByRole('button', { name: 'Go to the dashboard' }).click();
    await expect(page.getByRole('heading', { name: "You're connected" })).toHaveCount(0);
    const stored = await page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY);
    expect(stored).toBe(UI_TOKEN);
  });

  test('wrong key shows a clean inline error, never a raw status', async ({ page }) => {
    await page.route(api('setup/detect$'), (r) => json(r, 200, DETECT_OK));
    await page.route(api('setup/connect$'), (r) =>
      json(r, 400, {
        ok: false,
        code: 'auth_failed',
        error: 'The controller rejected those credentials. Double-check the API key and try again.',
      }),
    );

    await page.goto(BASE);
    await page.getByLabel('Controller address').fill('https://192.168.1.1');
    await page.getByRole('button', { name: 'Detect' }).click();
    await page.getByLabel('Paste your API key', { exact: true }).fill('wrong-key');
    await page.getByRole('button', { name: 'Connect' }).click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    const text = (await alert.textContent()) ?? '';
    expect(text).toContain('rejected those credentials');
    expect(text).not.toMatch(/\b400\b|Bad Request|Internal Server/);
    // Still on step 1, token never shown.
    await expect(page.getByRole('heading', { name: "You're connected" })).toHaveCount(0);
  });

  test('unreachable console keeps the user on the host step with honest copy', async ({
    page,
  }) => {
    await page.route(api('setup/detect$'), (r) => json(r, 200, DETECT_UNREACHABLE));

    await page.goto(BASE);
    await page.getByLabel('Controller address').fill('https://10.255.255.1');
    await page.getByRole('button', { name: 'Detect' }).click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    expect((await alert.textContent()) ?? '').toMatch(/Nothing answered/);
    // No key field appears for an unreachable console.
    await expect(page.getByLabel('Paste your API key', { exact: true })).toHaveCount(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Configured install → the dashboard just loads (§18.1, no read gate)
// ---------------------------------------------------------------------------

test.describe('Configured install just works', () => {
  test.beforeEach(async ({ page }) => {
    await freshLoad(page);
    await mockAppApis(page);
    await page.route(api('setup/status$'), (r) =>
      json(r, 200, { configured: true, controller_connected: true }),
    );
  });

  test('renders the app directly with no token wall (reads are open)', async ({ page }) => {
    await page.goto(BASE);

    // §18.1: a configured daemon loads the dashboard for any device on the LAN,
    // with no stored token. The app shell (sidebar nav) is present.
    await expect(page.getByRole('navigation')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Settings' })).toBeVisible();

    // The removed returning-user gate never appears; neither does the setup flow.
    await expect(
      page.getByRole('heading', { name: 'Welcome back to UnifiOptimizer' }),
    ).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Connect your network' })).toHaveCount(0);
    // And the just-in-time prompt is NOT open — viewing needs no token.
    await expect(page.getByRole('dialog')).toHaveCount(0);
    // No token was stored to view it.
    const stored = await page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY);
    expect(stored).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 4. Just-in-time token prompt on a mutating action (§18.1)
// ---------------------------------------------------------------------------

test.describe('Just-in-time access-token prompt', () => {
  test.beforeEach(async ({ page }) => {
    await freshLoad(page);
    await mockAppApis(page);
    await page.route(api('setup/status$'), (r) =>
      json(r, 200, { configured: true, controller_connected: true }),
    );
  });

  // Reach Settings via in-app nav (a dev deep-link to /settings falls back to /).
  // The page title is the h2 (the app-shell top bar also shows an h1 "Settings").
  const settingsTitle = (page: Page) =>
    page.getByRole('heading', { name: 'Settings', level: 2 });

  async function gotoSettings(page: Page) {
    await page.goto(BASE);
    await page.getByRole('link', { name: 'Settings' }).click();
    await expect(settingsTitle(page)).toBeVisible();
  }

  test('a token-gated action 401s → modal over the live view → retry succeeds', async ({
    page,
  }) => {
    // The reveal is token-gated from another device: 401 without the token, 200
    // once the Authorization header carries it (mirrors the middleware).
    await page.route(api('system/token$'), (r) => {
      const auth = r.request().headers()['authorization'];
      if (auth === `Bearer ${UI_TOKEN}`) {
        return json(r, 200, { token: UI_TOKEN, configured: true });
      }
      return json(r, 401, { detail: 'authentication required', code: 'unauthorized' });
    });

    await gotoSettings(page);

    // Settings loaded (viewing open). Trigger the token-gated reveal.
    const revealBtn = page.getByRole('button', { name: 'Reveal' });
    await expect(revealBtn).toBeVisible();
    await revealBtn.click();

    // The just-in-time modal appears — the Settings view stays visible underneath.
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('heading', { name: 'Enter your access token' })).toBeVisible();
    await expect(settingsTitle(page)).toBeVisible();

    // Enter the token and continue; the action retries and the token reveals.
    await dialog.getByPlaceholder('Access token').fill(UI_TOKEN);
    await dialog.getByRole('button', { name: 'Save & continue' }).click();

    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByText(UI_TOKEN)).toBeVisible();
    // The token is now stored for this browser.
    const stored = await page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY);
    expect(stored).toBe(UI_TOKEN);
  });

  test('dismissing the prompt leaves you exactly where you were', async ({ page }) => {
    await page.route(api('system/token$'), (r) =>
      json(r, 401, { detail: 'authentication required', code: 'unauthorized' }),
    );

    await gotoSettings(page);
    await page.getByRole('button', { name: 'Reveal' }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();

    // Modal gone, still on Settings, no token stored — viewing never blocked.
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(settingsTitle(page)).toBeVisible();
    const stored = await page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY);
    expect(stored).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 3. Daemon unreachable → honest error with retry (no wrong guess)
// ---------------------------------------------------------------------------

test.describe('Daemon unreachable', () => {
  test('status failure shows an honest retry, then recovers', async ({ page }) => {
    await freshLoad(page);
    await mockAppApis(page);

    // Fail every status read (StrictMode double-invokes the mount effect in dev,
    // so a counter is unreliable) until the test swaps in a healthy route.
    const statusRoute = api('setup/status$');
    const fail = (r: Route) => r.abort('failed');
    await page.route(statusRoute, fail);

    await page.goto(BASE);
    await expect(page.getByRole('heading', { name: "Can't reach UnifiOptimizer" })).toBeVisible();

    await page.unroute(statusRoute, fail);
    await page.route(statusRoute, (r) =>
      json(r, 200, { configured: false, controller_connected: false }),
    );

    await page.getByRole('button', { name: 'Try again' }).click();
    await expect(page.getByRole('heading', { name: 'Connect your network' })).toBeVisible();
  });
});
