import { defineConfig } from '@playwright/test';

const externalUrl = process.env.PLAYWRIGHT_BASE_URL;
const baseURL = externalUrl ?? 'http://127.0.0.1:4178';

export default defineConfig({
  testDir: './tests/e2e',
  use: { baseURL },
  projects: [
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
  ],
  webServer: externalUrl ? undefined : {
    command: 'npm run dev -- --hostname 127.0.0.1 --port 4178',
    url: baseURL,
  },
});
