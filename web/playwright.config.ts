import { defineConfig, devices } from "@playwright/test"

const externalUrl = process.env.PLAYWRIGHT_BASE_URL
const port = process.env.SWB_PLAYWRIGHT_PORT ?? "4177"
const baseURL = externalUrl ?? `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: "./tests",
  testMatch: ["mobile-navigation.spec.ts", "bot-identity.spec.ts"],
  timeout: 60_000,
  use: {
    ...devices["iPhone 13"],
    browserName: "chromium",
    viewport: { width: 390, height: 844 },
    baseURL,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  webServer: externalUrl ? undefined : {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
})
