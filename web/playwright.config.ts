import { defineConfig, devices } from "@playwright/test"

const externalUrl = process.env.PLAYWRIGHT_BASE_URL
const baseURL = externalUrl ?? "http://127.0.0.1:4177"

export default defineConfig({
  testDir: "./tests",
  testMatch: "mobile-navigation.spec.ts",
  timeout: 60_000,
  use: {
    ...devices["iPhone 13"],
    browserName: "chromium",
    viewport: { width: 390, height: 844 },
    baseURL,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  webServer: externalUrl ? undefined : {
    command: "npm run dev -- --host 127.0.0.1 --port 4177 --strictPort",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
})
