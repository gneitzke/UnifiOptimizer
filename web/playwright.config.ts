import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  workers: 1,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
  },
  projects: [
    {
      name: 'auth-setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'login-tests',
      testMatch: /login\.spec\.ts/,
    },
    {
      name: 'app-tests',
      testMatch: /app\.spec\.ts/,
      dependencies: ['auth-setup'],
      use: {
        storageState: 'e2e/.auth/state.json',
      },
    },
  ],
  webServer: {
    command: 'npx vite --port 5173',
    port: 5173,
    reuseExistingServer: true,
    timeout: 10_000,
  },
});
