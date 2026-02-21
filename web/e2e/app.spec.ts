import { test, expect } from '@playwright/test';

const BASE = 'http://localhost:5173';

// Auth state is pre-loaded via auth.setup.ts (storageState)

// ---------------------------------------------------------------------------
// 1. DASHBOARD
// ---------------------------------------------------------------------------

test.describe('Dashboard page', () => {
  test('renders dashboard with content', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await expect(page.getByText('Dashboard')).toBeVisible();
    const body = await page.textContent('body');
    expect(body!.length).toBeGreaterThan(50);
  });

  test('stays on dashboard after refresh', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.reload();
    await page.waitForTimeout(5000);
    expect(page.url()).toContain('/dashboard');
  });
});

// ---------------------------------------------------------------------------
// 2. SIDEBAR NAVIGATION
// ---------------------------------------------------------------------------

test.describe('Sidebar navigation', () => {
  test('navigate to history page', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('a[href="/history"]').click();
    await page.waitForURL('**/history');
    expect(page.url()).toContain('/history');
  });

  test('navigate to repair page', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('a[href="/repair"]').click();
    await page.waitForURL('**/repair');
    expect(page.url()).toContain('/repair');
  });

  test('navigate to analysis page', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('a[href*="analysis"]').click();
    await page.waitForURL('**/analysis/**');
    expect(page.url()).toContain('/analysis');
  });

  test('navigate between pages', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('a[href="/history"]').click();
    await page.waitForURL('**/history');
    await page.locator('a[href="/dashboard"]').click();
    await page.waitForURL('**/dashboard');
    expect(page.url()).toContain('/dashboard');
  });
});

// ---------------------------------------------------------------------------
// 3. LOGOUT
// ---------------------------------------------------------------------------

test.describe('Logout flow', () => {
  test('logout button navigates to login', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    const logoutBtn = page.locator('button[aria-label="Logout"]');
    await expect(logoutBtn).toBeVisible();
    await logoutBtn.click();
    await page.waitForURL(BASE + '/', { timeout: 5000 });
    await expect(page.getByText('Enter Manually')).toBeVisible();
  });

  test('after logout, protected routes redirect to login', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('button[aria-label="Logout"]').click();
    await page.waitForURL(BASE + '/');
    await page.goto(`${BASE}/dashboard`);
    await page.waitForTimeout(2000);
    expect(page.url()).toBe(`${BASE}/`);
  });

  test('token is cleared after logout', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('button[aria-label="Logout"]').click();
    await page.waitForURL(BASE + '/');
    const token = await page.evaluate(() =>
      localStorage.getItem('unifi_token'),
    );
    expect(token).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 4. HISTORY PAGE
// ---------------------------------------------------------------------------

test.describe('History page', () => {
  test('renders history page', async ({ page }) => {
    await page.goto(`${BASE}/history`);
    await page.waitForURL('**/history', { timeout: 15000 });
    const body = await page.textContent('body');
    expect(body).toBeTruthy();
  });

  test('stays on history after refresh', async ({ page }) => {
    await page.goto(`${BASE}/history`);
    await page.waitForURL('**/history', { timeout: 15000 });
    await page.reload();
    await page.waitForTimeout(5000);
    expect(page.url()).toContain('/history');
  });
});

// ---------------------------------------------------------------------------
// 5. REPAIR PAGE
// ---------------------------------------------------------------------------

test.describe('Repair page', () => {
  test('renders repair page', async ({ page }) => {
    await page.goto(`${BASE}/repair`);
    await page.waitForURL('**/repair', { timeout: 15000 });
    const body = await page.textContent('body');
    expect(body).toBeTruthy();
  });

  test('stays on repair after refresh', async ({ page }) => {
    await page.goto(`${BASE}/repair`);
    await page.waitForURL('**/repair', { timeout: 15000 });
    await page.reload();
    await page.waitForTimeout(5000);
    expect(page.url()).toContain('/repair');
  });
});

// ---------------------------------------------------------------------------
// 6. ANALYSIS PAGE
// ---------------------------------------------------------------------------

test.describe('Analysis page', () => {
  test('navigating to analysis/new shows analysis UI', async ({ page }) => {
    await page.goto(`${BASE}/analysis/new`);
    await page.waitForURL('**/analysis/**', { timeout: 15000 });
    expect(page.url()).toContain('/analysis');
  });

  test('stays on analysis after refresh', async ({ page }) => {
    await page.goto(`${BASE}/analysis/new`);
    await page.waitForURL('**/analysis/**', { timeout: 15000 });
    await page.reload();
    await page.waitForTimeout(5000);
    expect(page.url()).toContain('/analysis');
  });
});

// ---------------------------------------------------------------------------
// 7. SESSION PERSISTENCE
// ---------------------------------------------------------------------------

test.describe('Session persistence', () => {
  test('valid token survives page reload', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    const token = await page.evaluate(() =>
      localStorage.getItem('unifi_token'),
    );
    expect(token).toBeTruthy();
    await page.reload();
    await page.waitForTimeout(5000);
    expect(page.url()).toContain('/dashboard');
  });

  test('navigating between pages preserves auth', async ({ page }) => {
    await page.goto(`${BASE}/dashboard`);
    await page.waitForURL('**/dashboard', { timeout: 15000 });
    await page.locator('a[href="/history"]').click();
    await page.waitForURL('**/history');
    await page.locator('a[href="/repair"]').click();
    await page.waitForURL('**/repair');
    await page.locator('a[href="/dashboard"]').click();
    await page.waitForURL('**/dashboard');
    const token = await page.evaluate(() =>
      localStorage.getItem('unifi_token'),
    );
    expect(token).toBeTruthy();
  });
});
