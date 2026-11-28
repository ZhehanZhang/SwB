import { expect, test, type Page } from "@playwright/test"
import { layoutShiftBudget } from "./known-layout-shifts.ts"

type Seed = "busy" | "empty" | "error"
type Scenario = {
  name: string
  path: string
  seeds?: readonly Seed[]
  mobile?: boolean
  signedOut?: boolean
  selectedTab?: string
  prepare?: (page: Page) => Promise<void>
  ready?: (page: Page, seed: Seed) => Promise<void>
  interact?: (page: Page) => Promise<void>
}
type VisualWindow = Window & { visualShifts?: { sum: number; sources: string[]; capturing: boolean } }

const widths = [375, 768, 1024, 1440, 1920] as const
const themes = ["light", "dark"] as const
const all = ["busy", "empty", "error"] as const
const backupPath = "/memory?view=backup"

function button(page: Page, name: string | RegExp) {
  return page.getByRole("button", { name, exact: true })
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle")
  await expect(page.getByRole("status").filter({ hasText: /^(Loading|Checking)\b/ })).toHaveCount(0)
  await page.evaluate(async () => {
    await document.fonts.ready
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  })
}

async function expectActiveTab(page: Page, name: string) {
  await expect(page.getByRole("tab", { name, exact: true })).toHaveAttribute("aria-selected", "true")
  await expect(page.getByRole("tabpanel", { name, exact: true })).toBeVisible()
}

function backupStatus(overrides: Record<string, string | number | boolean | null>) {
  return async (page: Page) => {
    await page.route("**/api/memory/backup", async (route) => {
      const response = await route.fetch()
      await route.fulfill({ response, json: { ...await response.json(), ...overrides } })
    })
  }
}

const configured = { enabled: true, credential_saved: true }
const backupFailed = { ...configured, last_error: "The remote rejected the push." }
const login = async (page: Page) => {
  await expect(button(page, "Sign in with a passkey")).toBeVisible()
}

async function pendingApprovals(page: Page, seed: Seed) {
  if (seed === "busy") await expect(button(page, "Approve")).toHaveCount(12)
}

async function openNote(page: Page) {
  await button(page, "note-054").click()
  await expect(page.getByRole("dialog").getByRole("button", { name: "Edit", exact: true })).toBeVisible()
}

async function commandMenu(page: Page, query?: string) {
  await page.keyboard.press("Control+k")
  const dialog = page.getByRole("dialog", { name: "Search" })
  await expect(dialog).toBeVisible()
  if (query) {
    await dialog.getByRole("combobox").fill(query)
    await expect(dialog.getByRole(query === "zzzzz" ? "status" : "option").first()).toBeVisible()
  }
}

const agentSheets = [
  ["routine", "release-captain"],
  ["grok-routine", "grok-triage"],
  ["captain", "jam_EXAMPLEMERIDIAN"],
  ["dot", "example-dot"],
  ["key", "ak_000000000009"],
  ["mailbox", "northwind-procurement-assistant-staging"],
] as const

const scenarios: Scenario[] = [
  { name: "overview", path: "/", seeds: all },
  { name: "agents", path: "/agents", seeds: all },
  { name: "tokens", path: "/tokens", seeds: all },
  { name: "inbox", path: "/inbox", seeds: all, ready: pendingApprovals },
  { name: "inbox-thread", path: "/inbox/h_seed_0003", seeds: ["busy", "error"] },
  { name: "inbox-project", path: "/inbox?project=apac-edge-failover", seeds: ["busy", "error"], ready: pendingApprovals },
  { name: "memory", path: "/memory", seeds: all },
  { name: "memory-project", path: "/memory?scope=project%3Aoperations" },
  { name: "memory-agent", path: "/memory?scope=agent%3Aclaude-code" },
  { name: "memory-backup", path: backupPath, seeds: all },
  { name: "audit", path: "/audit", seeds: all },
  { name: "settings", path: "/settings", seeds: all },
  { name: "about", path: "/about", seeds: all },
  { name: "login", path: "/login", signedOut: true, ready: login },
  {
    name: "login-error", path: "/login?error=oidc_failed", seeds: ["error"], signedOut: true, ready: async (page) => {
      await login(page)
      await expect(page.getByRole("alert")).toContainText("Sign-in with your provider failed.")
    },
  },
  { name: "project", path: "/projects/operations", seeds: ["busy", "error"] },
  { name: "project-long", path: "/projects/apac-edge-failover" },
  { name: "project-missing", path: "/projects/nope", seeds: ["empty"] },
  { name: "project-modes", path: "/projects/operations?tab=modes" },
  ...["slack", "discord", "telegram", "github", "webhook"].map((platform) => ({
    name: `connections-${platform}`, path: `/connections/${platform}`, seeds: all,
  })),
  ...agentSheets.map(([kind, id]) => ({
    name: `agent-${kind}`, path: `/agents/${id}`, ready: async (page: Page) => {
      await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute("aria-selected", "true")
    },
  })),
  { name: "agent-tab-routines", path: "/agents/release-captain", selectedTab: "Routines" },
  { name: "agent-tab-tokens", path: "/agents/ak_000000000009", selectedTab: "Tokens" },
  { name: "agent-tab-activity", path: "/agents/jam_EXAMPLEMERIDIAN", selectedTab: "Activity" },
  ...["Routes", "Tokens", "Memory", "Messages"].map((name) => ({
    name: `audit-${name.toLowerCase()}`, path: "/audit", selectedTab: name,
  })),
  {
    name: "dialog-command-menu", path: "/agents", interact: (page) => commandMenu(page),
  },
  {
    name: "dialog-command-menu-query", path: "/agents", interact: (page) => commandMenu(page, "release"),
  },
  {
    name: "dialog-command-menu-empty", path: "/agents", interact: (page) => commandMenu(page, "zzzzz"),
  },
  {
    name: "sheet-mobile-more", path: "/", mobile: true, interact: async (page) => {
      await button(page, "More").click()
      await expect(page.getByRole("dialog", { name: "More" })).toBeVisible()
    },
  },
  {
    name: "dialog-token-revoke", path: "/tokens", interact: async (page) => {
      await button(page, /^Revoke access key/).first().click()
      await expect(page.getByRole("alertdialog", { name: "Revoke access key?" })).toBeVisible()
    },
  },
  {
    name: "agent-revoke-inline", path: "/agents/northwind-procurement-assistant-staging", interact: async (page) => {
      await page.getByRole("tab", { name: "Tokens", exact: true }).press("Space")
      await expectActiveTab(page, "Tokens")
      await button(page, "Revoke").first().click()
      await expect(button(page, "Revoke token")).toBeVisible()
    },
  },
  {
    name: "disclosure-pairing-code", path: "/tokens", interact: async (page) => {
      await page.getByText("Pairing code", { exact: true }).click()
      await expect(button(page, "Create code")).toBeVisible()
    },
  },
  {
    name: "disclosure-channel-mode", path: "/connections/discord", interact: async (page) => {
      await button(page, "Details for deployments").first().click()
    },
  },
  {
    name: "popover-channel-mode", path: "/connections/discord", interact: async (page) => {
      await button(page, "Mode for deployments").click()
      await expect(page.getByRole("menu")).toBeVisible()
    },
  },
  {
    name: "dialog-token-scope-remove", path: "/tokens", interact: async (page) => {
      await button(page, /^Remove .+ from /).first().click()
      await expect(page.getByRole("alertdialog", { name: /^Remove .+ access\?$/ })).toBeVisible()
    },
  },
  {
    name: "dialog-passkey-remove", path: "/settings", interact: async (page) => {
      await button(page, "Remove Fixture passkey").click()
      await expect(page.getByRole("alertdialog", { name: "Remove Fixture passkey?" })).toBeVisible()
    },
  },
  {
    name: "dialog-memory-note", path: "/memory", interact: openNote,
  },
  ...["Move", "Edit", "Delete"].map((action) => ({
    name: `dialog-memory-note-${action.toLowerCase()}`, path: "/memory", interact: async (page: Page) => {
      await openNote(page)
      await button(page, action).click()
      await expect(page.getByRole("form", { name: action === "Edit" ? "Edit note" : `Confirm ${action.toLowerCase()}` })).toBeVisible()
    },
  })),
  {
    name: "dialog-memory-note-rename", path: "/memory", interact: async (page) => {
      await openNote(page)
      await button(page, "Rename path").click()
      await expect(page.getByRole("form", { name: "Confirm rename" })).toBeVisible()
    },
  },
  {
    name: "memory-search", path: "/memory", interact: async (page) => {
      await button(page, "Search notes").click()
      await expect(page.getByRole("search", { name: "Memory" })).toBeVisible()
    },
  },
  {
    name: "memory-search-empty", path: "/memory", interact: async (page) => {
      await button(page, "Search notes").click()
      const search = page.getByRole("search", { name: "Memory" }).getByRole("searchbox")
      await search.fill("zzzzz")
      await search.press("Enter")
      await expect(page.getByRole("status").filter({ hasText: "0 results" })).toBeVisible()
    },
  },
  {
    name: "memory-backup-configure", path: backupPath, seeds: ["empty"], interact: async (page) => {
      await button(page, "Configure backup").click()
      await expect(page.getByRole("textbox", { name: "Repository address" })).toBeVisible()
    },
  },
  { name: "memory-backup-configured", path: backupPath, prepare: backupStatus(configured) },
  {
    name: "memory-backup-awaiting", path: backupPath,
    prepare: backupStatus({ ...configured, last_attempt_at: null, last_success_at: null, last_commit: null }),
  },
  { name: "memory-backup-failed", path: backupPath, prepare: backupStatus(backupFailed) },
  {
    name: "memory-backup-credential-failed", path: backupPath,
    prepare: backupStatus({ ...backupFailed, last_error: "Authentication failed." }),
  },
  {
    name: "memory-backup-git-missing", path: backupPath,
    prepare: backupStatus({ git_available: false }),
  },
  {
    name: "memory-backup-edit", path: backupPath, prepare: backupStatus(configured), interact: async (page) => {
      await button(page, "Edit settings").click()
      await expect(page.getByRole("textbox", { name: "Author name" })).toBeVisible()
    },
  },
  {
    name: "dialog-memory-restore", path: backupPath, interact: async (page) => {
      await button(page, "Restore from repository").click()
      await expect(page.getByRole("alertdialog", { name: "Restore from repository?" })).toBeVisible()
    },
  },
  {
    name: "settings-add-passkey", path: "/settings", interact: async (page) => {
      await button(page, "Add a passkey").click()
      await expect(page.getByRole("textbox", { name: "Passkey name" })).toBeVisible()
    },
  },
  {
    name: "settings-pair-device", path: "/settings", prepare: async (page) => {
      await page.route("**/pairing/companion", (route) =>
        route.fulfill({ json: { code: "ABCDE-FGHJK", expires_in_s: 600 } }))
    }, interact: async (page) => {
      await button(page, "Pair a device").click()
      await expect(page.getByLabel("Pairing code", { exact: true })).toHaveText("ABCDE-FGHJK")
    },
  },
  {
    name: "toast-copy", path: "/", interact: async (page) => {
      await button(page, "Copy MCP URL").click()
      await expect(page.getByText("MCP URL copied", { exact: true })).toBeVisible()
    },
  },
]

const routeNames = new Map(scenarios
  .filter(({ interact, prepare, selectedTab }) => !interact && !prepare && !selectedTab)
  .map(({ path, name }) => [path, name]))

for (const scenario of scenarios) {
  for (const width of scenario.mobile ? [375] : widths) for (const theme of themes) {
    const tags = (scenario.seeds ?? ["busy"]).map((seed) => `@${seed}`)
    const interaction = scenario.interact || scenario.selectedTab
    if (interaction || scenario.prepare) tags.push("@view-state")
    test(`${scenario.name} ${width} ${theme}`, { tag: tags }, async ({ page }, info) => {
      const seed = info.project.name as Seed
      const now = Number(info.project.metadata.now)
      await page.setViewportSize({ width, height: width === 375 ? 812 : 900 })
      await page.emulateMedia({ colorScheme: theme })
      if (!scenario.signedOut) await page.setExtraHTTPHeaders({ "cf-access-jwt-assertion": "channel-mode-fixture" })
      await page.clock.install({ time: now })
      await page.clock.setFixedTime(now)
      const agentInspector = width >= 768 && scenario.path.startsWith("/agents/")
      await page.addInitScript(({ theme, agentInspector }) => {
        localStorage.setItem("swb-theme", theme)
        const shifts = { sum: 0, sources: [] as string[], capturing: false }
        Object.assign(window, { visualShifts: shifts })
        if (agentInspector) {
          const nativeMatchMedia = window.matchMedia.bind(window)
          window.matchMedia = (query) => {
            const media = nativeMatchMedia(query)
            media.addEventListener("change", (event) => {
              if (shifts.capturing) event.stopImmediatePropagation()
            })
            return media
          }
        }
        new PerformanceObserver((entries) => {
          for (const entry of entries.getEntries() as (PerformanceEntry & {
            hadRecentInput: boolean
            value: number
            sources: Array<{ node?: Node }>
          })[]) {
            if (entry.hadRecentInput) continue
            shifts.sum += entry.value
            if (shifts.sources.length < 12) {
              shifts.sources.push(...entry.sources.map(({ node }) => node instanceof Element
                ? [node.tagName, ...Array.from(node.classList).slice(0, 3)].join(".")
                : node?.nodeName ?? "unknown"))
            }
          }
        }).observe({ type: "layout-shift", buffered: true })
      }, { theme, agentInspector })
      await scenario.prepare?.(page)
      const response = await page.goto(scenario.path, { waitUntil: "networkidle" })
      expect(response?.ok(), `document response for ${scenario.path}`).toBe(true)
      await expect(page.locator("main")).toBeVisible()
      if (!scenario.signedOut) await expect(page).not.toHaveURL(/\/login(?:\?|$)/)
      await scenario.ready?.(page, seed)
      if (seed === "error") await expect(page.getByRole("alert").first()).toBeVisible()
      await settle(page)
      const shifts = await page.evaluate(() => (window as VisualWindow).visualShifts)
      const name = `${scenario.name}-${width}-${theme}`
      const route = routeNames.get(scenario.path) ?? scenario.name
      const budget = process.env.SWB_VISUAL_STRICT_CLS === "1" ? 0.05 : layoutShiftBudget(seed, route, width)
      if (budget > 0.05) {
        info.annotations.push({ type: "audit L5", description: `PR B layout debt capped at ${budget}` })
      }
      expect.soft(shifts?.sum, `${seed}/${name} CLS on ${scenario.path}`).toBeLessThanOrEqual(budget)
      if (scenario.selectedTab) {
        await page.getByRole("tab", { name: scenario.selectedTab, exact: true }).press("Space")
      }
      await scenario.interact?.(page)
      if (interaction) await settle(page)
      if (scenario.selectedTab) await expectActiveTab(page, scenario.selectedTab)
      const metrics = await page.evaluate(() => ({ documentWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth }))
      await info.attach("layout-metrics", {
        body: JSON.stringify({ seed, name, route, cls: shifts?.sum, sources: shifts?.sources, ...metrics, budget }),
        contentType: "application/json",
      })
      expect.soft(metrics.documentWidth, `${name} horizontal overflow`).toBeLessThanOrEqual(metrics.viewportWidth)
      const screenshotWidth = width === 375 || width === 1024 || width === 1440
        || (width === 1920 && seed === "busy" && scenario.name === "inbox-thread")
      if (!screenshotWidth) return
      if (agentInspector) await page.evaluate(() => { (window as VisualWindow).visualShifts!.capturing = true })
      try {
        await expect(page).toHaveScreenshot(`${name}.png`, {
          fullPage: true,
          mask: [
            page.getByText(/^up \d+ /),
            page.getByRole("region", { name: "Hosts", exact: true }).locator("tbody td").nth(1),
            page.locator('a[href*="/commit/"]'),
          ],
        })
        if (scenario.selectedTab) await expectActiveTab(page, scenario.selectedTab)
      } finally {
        if (agentInspector) await page.evaluate(() => { (window as VisualWindow).visualShifts!.capturing = false })
      }
    })
  }
}
