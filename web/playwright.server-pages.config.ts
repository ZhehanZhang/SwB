import { defineConfig } from "@playwright/test"

const baseURL = `http://127.0.0.1:${process.env.SWB_SERVER_PAGES_PORT ?? "4184"}`

export default defineConfig({
  testDir: "./tests",
  testMatch: "server-pages.visual.spec.ts",
  use: {
    baseURL,
    browserName: "chromium",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  webServer: {
    command: `${process.env.PLAYWRIGHT_PYTHON ?? "../.venv/bin/python"} tests/server_pages_fixture.py`,
    url: `${baseURL}/healthz`,
    reuseExistingServer: false,
  },
})
