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
      // First-run onboarding (§18). Self-contained: every /api/setup/* call is
      // mocked with page.route, so it needs no backend and no auth-setup.
      name: 'setup-tests',
      testMatch: /setup-flow\.spec\.ts/,
    },
    {
      // Report export action (§19). Self-contained: /api/setup/status is mocked
      // configured and every other read is stubbed, so it needs no backend and no
      // auth-setup. Guards the export button, the sidebar destination, and that
      // the app chrome drops out of the print output.
      name: 'report-export-tests',
      testMatch: /report-export\.spec\.ts/,
    },
    {
      // Report page (§19). Self-contained: GET /api/report and the setup gate are
      // mocked from the demo fixture, so it needs no backend and no auth-setup.
      // Renders the model as given and emits the print-to-PDF + screenshots.
      name: 'report-page-tests',
      testMatch: /report\.spec\.ts/,
    },
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
