import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'node e2e/support/server.mjs',
      url: 'http://127.0.0.1:4174/__control/state',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'node e2e/support/app-server.mjs',
      url: 'http://127.0.0.1:4175/__app_control/state',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
