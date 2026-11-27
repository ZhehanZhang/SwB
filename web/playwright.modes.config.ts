import { defineConfig } from "@playwright/test"

const baseURL = `http://localhost:${process.env.SWB_PLAYWRIGHT_PORT ?? "4178"}`

export default defineConfig({
  testDir: "./tests",
  testMatch: ["channel-modes.spec.ts", "inbox-senders.spec.ts", "owner-flows.spec.ts"],
  use: {
    baseURL,
    browserName: "chromium",
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
    extraHTTPHeaders: { "cf-access-jwt-assertion": "channel-mode-fixture" },
  },
  webServer: {
    command: `${process.env.PLAYWRIGHT_PYTHON ?? "../.venv/bin/python"} tests/channel_modes_fixture.py`,
    url: `${baseURL}/auth/session`,
  },
})
