import { defineConfig } from "@playwright/test"

const now = "1768478400"
const seeds = { busy: 4190, empty: 4191, error: 4192 } as const
const python = process.env.PLAYWRIGHT_PYTHON ?? "../.venv/bin/python"
const artifacts = process.env.SWB_VISUAL_DATA ? "/artifacts" : "test-results/visual"

export default defineConfig({
  testDir: "./tests/visual",
  testMatch: "routes.spec.ts",
  forbidOnly: !!process.env.CI,
  fullyParallel: true,
  updateSnapshots: "none",
  workers: Number(process.env.SWB_VISUAL_WORKERS ?? 2),
  outputDir: `${artifacts}/results`,
  snapshotPathTemplate: "{testDir}/__screenshots__/{projectName}/{arg}{ext}",
  reporter: [
    ["line"],
    ["html", { outputFolder: `${artifacts}/report`, open: "never" }],
    ["json", { outputFile: `${artifacts}/metrics.json` }],
  ],
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
      maxDiffPixelRatio: 0.002,
      threshold: 0.2,
    },
  },
  use: {
    browserName: "chromium",
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "UTC",
    reducedMotion: "reduce",
    permissions: ["clipboard-read", "clipboard-write"],
    trace: "retain-on-failure",
  },
  projects: Object.entries(seeds).map(([name, port]) => ({
    name,
    grep: new RegExp(`@${name}\\b`),
    use: { baseURL: `http://localhost:${port}` },
    metadata: { now: Number(now) * 1_000 },
  })),
  webServer: Object.entries(seeds).map(([seed, port]) => ({
    command: `${python} tests/visual_fixture.py`,
    env: { SWB_SEED: seed, SWB_NOW: now, SWB_PLAYWRIGHT_PORT: String(port) },
    url: `http://localhost:${port}/healthz`,
  })),
})
