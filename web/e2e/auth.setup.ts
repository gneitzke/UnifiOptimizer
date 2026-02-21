import { test as setup, expect } from '@playwright/test';

const BASE = 'http://localhost:5173';
const HOST = 'https://192.168.1.1';
const USER = 'audit';
const PASS = 'CHANGE_ME';

setup('authenticate', async ({ page }) => {
  await page.goto(BASE);
  await page.evaluate(() => localStorage.clear());
  await page.goto(BASE);

  await page.getByText('Enter Manually').click();
  await page.getByPlaceholder('https://192.168.1.1:8443').fill(HOST);
  await page.getByRole('button', { name: 'Continue' }).click();

  await expect(page.getByText('Connecting to')).toBeVisible();
  const usernameInput = page.locator(
    'input:not([type="password"]):not([type="checkbox"])',
  );
  await usernameInput.fill(USER);
  await page.locator('input[type="password"]').fill(PASS);

  await page.getByRole('button', { name: /Sign In/i }).click();
  await page.waitForURL('**/dashboard', { timeout: 30000 });

  // Save auth state (localStorage with token)
  await page.context().storageState({
    path: 'e2e/.auth/state.json',
  });
});
