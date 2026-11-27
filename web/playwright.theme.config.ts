import { tmpdir } from "node:os"
import { join } from "node:path"
import { defineConfig } from "@playwright/test"

const baseURL = "http://localhost:4178"

export default defineConfig({
  testDir: "./tests",
  testMatch: "theme-browser.spec.ts",
  outputDir: join(tmpdir(), "swb-theme-browser-results"),
  use: {
    baseURL,
    extraHTTPHeaders: { "cf-access-jwt-assertion": "channel-mode-fixture" },
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium", launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } },
    },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
  webServer: {
    command: `${process.env.PLAYWRIGHT_PYTHON ?? "../.venv/bin/python"} tests/channel_modes_fixture.py`,
    url: `${baseURL}/auth/session`,
    reuseExistingServer: true,
  },
})
