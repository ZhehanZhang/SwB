import { defineConfig } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:4173';

export default defineConfig({
  testDir: './tests/e2e',
  use: { baseURL, browserName: 'chromium' },
  projects: [
    { name: '390', use: { viewport: { width: 390, height: 844 } } },
    { name: '1440', use: { viewport: { width: 1440, height: 1000 } } },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : {
    command: 'next build && next start --hostname 127.0.0.1 --port 4173',
    timeout: 120_000,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
});
