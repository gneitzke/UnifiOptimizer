import { test, expect, Page } from '@playwright/test';

const BASE = 'http://localhost:5173';
const VALID_HOST = 'https://192.168.1.1';
const VALID_USER = 'audit';
const VALID_PASS = 'CHANGE_ME';
const BAD_HOST = 'https://10.255.255.1';
const BAD_USER = 'nobody';
const BAD_PASS = 'wrong';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function clearState(page: Page) {
  await page.goto(BASE);
  await page.evaluate(() => localStorage.clear());
  await page.goto(BASE);
}

async function expectChooseScreen(page: Page) {
  await expect(page.getByText('Enter Manually')).toBeVisible();
  await expect(page.getByText('Scan Network')).toBeVisible();
  await expect(page.getByText('UniFi Optimizer')).toBeVisible();
  await expect(page.getByText('Connect to your controller')).toBeVisible();
}

async function goManualAndFillHost(page: Page, host: string) {
  await page.getByText('Enter Manually').click();
  await expect(page.getByText('Controller URL')).toBeVisible();
  const urlInput = page.getByPlaceholder('https://192.168.1.1:8443');
  await urlInput.fill(host);
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByText('Connecting to')).toBeVisible();
}

async function fillCredsAndSubmit(page: Page, user: string, pass: string) {
  const usernameInput = page.locator(
    'input:not([type="password"]):not([type="checkbox"])',
  );
  await usernameInput.fill(user);
  await page.locator('input[type="password"]').fill(pass);
  await page.getByRole('button', { name: /Sign In/i }).click();
}

// ---------------------------------------------------------------------------
// 1. CHOOSE SCREEN
// ---------------------------------------------------------------------------

test.describe('Choose screen', () => {
  test.beforeEach(async ({ page }) => {
    await clearState(page);
  });

  test('renders both options on fresh load', async ({ page }) => {
    await expectChooseScreen(page);
  });

  test('clears stale token and shows choose screen', async ({ page }) => {
    // Inject a fake stale token
    await page.evaluate(() =>
      localStorage.setItem('unifi_token', 'stale.jwt.token'),
    );
    await page.goto(BASE);
    await page.waitForTimeout(1000);
    // Stale token fails validation, so login screen shows
    await expectChooseScreen(page);
  });
});

// ---------------------------------------------------------------------------
// 2. MANUAL ENTRY PATH
// ---------------------------------------------------------------------------

test.describe('Manual entry flow', () => {
  test.beforeEach(async ({ page }) => {
    await clearState(page);
  });

  test('enter manually → back returns to choose', async ({ page }) => {
    await page.getByText('Enter Manually').click();
    await expect(page.getByText('Controller URL')).toBeVisible();
    await page.getByText('Back').click();
    await expectChooseScreen(page);
  });

  test('continue button disabled when URL empty', async ({ page }) => {
    await page.getByText('Enter Manually').click();
    const continueBtn = page.getByRole('button', { name: 'Continue' });
    await expect(continueBtn).toBeDisabled();
  });

  test('continue button enabled after entering URL', async ({ page }) => {
    await page.getByText('Enter Manually').click();
    await page.getByPlaceholder('https://192.168.1.1:8443').fill(VALID_HOST);
    const continueBtn = page.getByRole('button', { name: 'Continue' });
    await expect(continueBtn).toBeEnabled();
  });

  test('manual → credentials shows host', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await expect(page.getByText(VALID_HOST)).toBeVisible();
  });

  test('credentials → back returns to choose', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await page.getByText('Back').click();
    await expectChooseScreen(page);
  });

  test('sign in disabled with empty username', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await page.locator('input[type="password"]').fill(VALID_PASS);
    const signIn = page.getByRole('button', { name: /Sign In/i });
    await expect(signIn).toBeDisabled();
  });

  test('sign in disabled with empty password', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await page.locator('input').nth(0).fill(VALID_USER);
    const signIn = page.getByRole('button', { name: /Sign In/i });
    await expect(signIn).toBeDisabled();
  });

  test('remember checkbox defaults to checked', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    const checkbox = page.locator('input[type="checkbox"]');
    await expect(checkbox).toBeChecked();
  });

  test('remember checkbox can be unchecked', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    const checkbox = page.locator('input[type="checkbox"]');
    await checkbox.uncheck();
    await expect(checkbox).not.toBeChecked();
  });
});

// ---------------------------------------------------------------------------
// 3. HAPPY PATH: Manual → valid creds → dashboard
// ---------------------------------------------------------------------------

test.describe('Manual login happy path', () => {
  test.beforeEach(async ({ page }) => {
    await clearState(page);
  });

  test('valid creds → navigates to dashboard and stays', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await fillCredsAndSubmit(page, VALID_USER, VALID_PASS);

    // Should show loading state
    await expect(
      page.getByRole('button', { name: /Connecting/i }),
    ).toBeVisible();

    // Wait for navigation
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    expect(page.url()).toContain('/dashboard');

    // Verify we STAY on dashboard (no flash-back)
    await page.waitForTimeout(2000);
    expect(page.url()).toContain('/dashboard');

    // Token should be stored
    const token = await page.evaluate(() =>
      localStorage.getItem('unifi_token'),
    );
    expect(token).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 4. NEGATIVE: bad credentials
// ---------------------------------------------------------------------------

test.describe('Login error handling', () => {
  test.beforeEach(async ({ page }) => {
    await clearState(page);
  });

  test('wrong password shows real backend error', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await fillCredsAndSubmit(page, VALID_USER, BAD_PASS);

    // Wait for error to appear
    const errorEl = page.locator('[class*="rounded-lg"]').filter({
      has: page.locator('svg'),
    });
    await expect(errorEl).toBeVisible({ timeout: 15000 });

    const errorText = await errorEl.textContent();
    // Must show real backend message, not generic "Authentication failed"
    expect(errorText).not.toContain('Authentication failed. Check your credentials.');
    expect(errorText!.length).toBeGreaterThan(5);
  });

  test('wrong username shows real backend error', async ({ page }) => {
    await goManualAndFillHost(page, VALID_HOST);
    await fillCredsAndSubmit(page, BAD_USER, VALID_PASS);

    const errorEl = page.locator('[class*="rounded-lg"]').filter({
      has: page.locator('svg'),
    });
    await expect(errorEl).toBeVisible({ timeout: 15000 });

    const errorText = await errorEl.textContent();
    expect(errorText).not.toContain('Authentication failed. Check your credentials.');
    expect(errorText!.length).toBeGreaterThan(5);
  });

  test('unreachable host shows connection error', async ({ page }) => {
    await goManualAndFillHost(page, BAD_HOST);
    await fillCredsAndSubmit(page, VALID_USER, VALID_PASS);

    const errorEl = page.locator('[class*="rounded-lg"]').filter({
      has: page.locator('svg'),
    });
    await expect(errorEl).toBeVisible({ timeout: 30000 });

    const errorText = await errorEl.textContent();
    // Should mention connection/reach issue
    expect(errorText).toBeTruthy();
    expect(errorText!.length).toBeGreaterThan(10);
  });
});

// ---------------------------------------------------------------------------
// 5. SCAN NETWORK PATH
// ---------------------------------------------------------------------------

test.describe('Scan network flow', () => {
  test.beforeEach(async ({ page }) => {
    await clearState(page);
  });

  test('scan shows scanning spinner', async ({ page }) => {
    await page.getByText('Scan Network').click();
    await expect(page.getByText('Scanning network…')).toBeVisible();
    // Back button visible during scan
    await expect(page.getByText('Back')).toBeVisible();
  });

  test('scan → back returns to choose', async ({ page }) => {
    await page.getByText('Scan Network').click();
    await page.getByText('Back').click();
    await expectChooseScreen(page);
  });

  test('scan completes and shows results with IP and hostname', async ({
    page,
  }) => {
    await page.getByText('Scan Network').click();
    await expect(page.getByText('Scanning network…')).toBeVisible();

    // Wait for scan to finish
    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 90000 },
    );

    // Should show results
    await expect(page.getByText('choose a connection')).toBeVisible();

    // Should have IP address entries
    const ipEntries = page.getByText(/IP address/);
    await expect(ipEntries.first()).toBeVisible();
    const ipCount = await ipEntries.count();
    expect(ipCount).toBeGreaterThan(0);

    // Should have hostname entries
    const hostnameEntries = page.getByText(/Hostname/);
    const hostnameCount = await hostnameEntries.count();
    expect(hostnameCount).toBeGreaterThan(0);

    // "Enter manually" option always visible
    await expect(page.getByText('Enter manually')).toBeVisible();
    await expect(page.getByText('Use a custom URL')).toBeVisible();
  });

  test('scan results → enter manually → manual host entry', async ({
    page,
  }) => {
    await page.getByText('Scan Network').click();
    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 90000 },
    );
    await page.getByText('Enter manually').click();
    await expect(page.getByText('Controller URL')).toBeVisible();
    await expect(
      page.getByPlaceholder('https://192.168.1.1:8443'),
    ).toBeVisible();
  });

  test('scan → select IP → credentials screen shows selected host', async ({
    page,
  }) => {
    await page.getByText('Scan Network').click();
    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 90000 },
    );

    // Click the first IP address option
    const firstIP = page.getByText(/IP address/).first();
    const ipButton = firstIP.locator('xpath=ancestor::button');
    // Get the host text from the button
    const hostLabel = await ipButton.locator('.text-sm').textContent();
    await ipButton.click();

    // Should be on credentials screen showing the host
    await expect(page.getByText('Connecting to')).toBeVisible();
    if (hostLabel) {
      await expect(page.getByText(hostLabel)).toBeVisible();
    }
  });

  test('scan → select hostname → credentials screen shows selected host', async ({
    page,
  }) => {
    await page.getByText('Scan Network').click();
    await page.waitForFunction(
      () => !document.querySelector('.animate-spin'),
      { timeout: 90000 },
    );

    const hostnameEntries = page.getByText(/Hostname/);
    const count = await hostnameEntries.count();
    if (count === 0) {
      test.skip();
      return;
    }
    const hostnameButton = hostnameEntries.first().locator('xpath=ancestor::button');
    const hostLabel = await hostnameButton.locator('.text-sm').textContent();
    await hostnameButton.click();

    await expect(page.getByText('Connecting to')).toBeVisible();
    if (hostLabel) {
      await expect(page.getByText(hostLabel)).toBeVisible();
    }
  });
});

// ---------------------------------------------------------------------------
// 6. SCAN → LOGIN end-to-end
// ---------------------------------------------------------------------------

test.describe('Scan → login end-to-end', () => {
  test.beforeEach(async ({ page }) => {
    await clearState(page);
  });

  test('scan → select controller IP → valid login → dashboard', async ({
    page,
  }) => {
    await page.getByText('Scan Network').click();
    // Wait for results to render (not just spinner gone)
    await expect(
      page.getByText('choose a connection'),
    ).toBeVisible({ timeout: 90000 });

    // Find and click the 192.168.1.1 IP option (port 443)
    const targetBtn = page
      .locator('button')
      .filter({ hasText: '192.168.1.1' })
      .filter({ hasText: 'IP address' })
      .first();
    await expect(targetBtn).toBeVisible();
    await targetBtn.click();

    await expect(page.getByText('Connecting to')).toBeVisible();
    await fillCredsAndSubmit(page, VALID_USER, VALID_PASS);

    await page.waitForURL('**/dashboard', { timeout: 15000 });
    expect(page.url()).toContain('/dashboard');

    // Stays on dashboard
    await page.waitForTimeout(2000);
    expect(page.url()).toContain('/dashboard');
  });

  test('scan → select controller IP → wrong password → error shown', async ({
    page,
  }) => {
    await page.getByText('Scan Network').click();
    await expect(
      page.getByText('choose a connection'),
    ).toBeVisible({ timeout: 90000 });

    const targetBtn = page
      .locator('button')
      .filter({ hasText: '192.168.1.1' })
      .filter({ hasText: 'IP address' })
      .first();
    await targetBtn.click();

    await fillCredsAndSubmit(page, VALID_USER, BAD_PASS);

    const errorEl = page.locator('[class*="rounded-lg"]').filter({
      has: page.locator('svg'),
    });
    await expect(errorEl).toBeVisible({ timeout: 15000 });
    const errorText = await errorEl.textContent();
    expect(errorText!.length).toBeGreaterThan(5);
  });
});

// ---------------------------------------------------------------------------
// 7. NAVIGATION GUARDS
// ---------------------------------------------------------------------------

test.describe('Navigation guards', () => {
  test('direct /dashboard access without login redirects to /', async ({
    page,
  }) => {
    await clearState(page);
    await page.goto(`${BASE}/dashboard`);
    await page.waitForTimeout(1000);
    expect(page.url()).toBe(`${BASE}/`);
    await expectChooseScreen(page);
  });

  test('direct /analysis/123 access without login redirects to /', async ({
    page,
  }) => {
    await clearState(page);
    await page.goto(`${BASE}/analysis/123`);
    await page.waitForTimeout(1000);
    expect(page.url()).toBe(`${BASE}/`);
  });

  test('direct /history access without login redirects to /', async ({
    page,
  }) => {
    await clearState(page);
    await page.goto(`${BASE}/history`);
    await page.waitForTimeout(1000);
    expect(page.url()).toBe(`${BASE}/`);
  });

  test('unknown route redirects to /', async ({ page }) => {
    await clearState(page);
    await page.goto(`${BASE}/nonexistent-page`);
    await page.waitForTimeout(1000);
    expect(page.url()).toBe(`${BASE}/`);
  });
});
