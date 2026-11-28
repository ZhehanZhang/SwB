import { expect, test, type Locator, type Page } from "@playwright/test"
import { PRODUCT_NAME, TAGLINE } from "../src/app/brand.ts"

const board = {
  runtime: { online: true, mcp_url: null, host: "fixture", version: null, uptime_seconds: 0 },
  projects: [{ slug: "ops", scope: "project:ops", name: "Operations", archived: false, created_at: null,
    turn_cap: null, memory_count: 0, open_count: 25, sources: [], agents: [] }],
  unassigned: [],
}
const inbox = {
  pending_writes: [], turn_cap: null, pauses: [],
  next_cursor: null,
  conversations: Array.from({ length: 25 }, (_, index) => ({
    handle: `task-${index}`, owner: "claude-code", destination: "capy", ref: `task-${index}`, project: "ops",
    title: `Fixture task ${index + 1}`, target: null, status: "working", open: true, created_at: null, updated_at: null,
  })),
}

test.beforeEach(async ({ page }) => {
  await page.route("**/auth/**", route => {
    if (new URL(route.request().url()).pathname === "/auth/session") {
      return route.fulfill({ json: { signed_in: true, via: "access", csrf: null, methods: { passkeys: 0, oidc: null, access: true } } })
    }
    return route.fulfill({ status: 404, json: { error: "not_found" } })
  })
  await page.route("**/api/**", route => {
    const { pathname } = new URL(route.request().url())
    if (pathname === "/api/board") return route.fulfill({ json: board })
    if (pathname === "/api/inbox") return route.fulfill({ json: inbox })
    if (pathname === "/api/companions") return route.fulfill({ json: { companions: [] } })
    if (pathname === "/api/about") return route.fulfill({ json: { version: "1.0.0", commit: "unknown", build_date: "unknown", blocks: [] } })
    return route.fulfill({ status: 404, json: { error: "not_found" } })
  })
})

async function tap(page: Page, locator: Locator, lowerEdge = false) {
  await expect(locator).toBeVisible()
  const box = await locator.boundingBox()
  if (!box) throw new Error("Touch target has no hit area")
  await page.touchscreen.tap(box.x + box.width / 2, box.y + (lowerEdge ? box.height + 11 : box.height / 2))
}

const navigation = (page: Page) => page.getByRole("navigation", { name: "Navigation" })
const menu = (page: Page) => page.getByRole("dialog", { name: "More" })

async function waitForSheet(page: Page) {
  const sheet = menu(page)
  await expect(sheet).toBeVisible()
  await sheet.evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)))
}

async function expectTab(page: Page, label: string, to: string) {
  await expect(page).toHaveURL(new RegExp(`/${to}$`))
  await expect(navigation(page).getByRole("link", { name: label, exact: true })).toHaveAttribute("aria-current", "page")
}

test("About and page metadata use the shared tagline", async ({ page }) => {
  await page.goto("/about")
  await expect(page.getByText(TAGLINE.en, { exact: true })).toBeVisible()
  await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", TAGLINE.en)
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", PRODUCT_NAME)
  await expect(page.locator('meta[property="og:description"]')).toHaveAttribute("content", TAGLINE.en)
})

test("Audit shows a webhook refusal reason as plain text", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1200, height: 800 })
  await page.route("**/api/audit?**", route => route.fulfill({ json: { next_cursor: null, items: [{
    id: 1, at: "2026-01-01T12:00:00Z", origin: "github", source: "-", destination: "github", action: "webhook",
    outcome: "refused", target: null, ref: null, detail: "invalid signature",
  }] } }))
  await page.goto("/audit")
  await expect(page.getByRole("columnheader", { name: "Detail" })).toBeVisible()
  const row = page.getByRole("row").filter({ hasText: "Refused webhook" })
  await expect(row.getByRole("cell", { name: "invalid signature", exact: true })).toBeVisible()
  await row.screenshot({ path: testInfo.outputPath("audit-refusal.png") })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(row.getByRole("cell", { name: "invalid signature", exact: true })).toBeVisible()
})

test("Connections shows companion devices and supports pairing and disconnecting", async ({ page }, testInfo) => {
  const csrf = "fixture-csrf"
  const code = "fixture-pairing-code"
  const url = "https://example.test/fixture-pairing-url"
  let companions = [
    { id: "device-1", name: "Studio Mac", platform: "darwin", online: true, last_seen: 1, capabilities: ["notify"] },
    { id: "device-2", name: "Workshop Laptop", platform: "linux", online: false, last_seen: 0, capabilities: ["notify"] },
  ]
  await page.route("**/auth/session", route => route.fulfill({
    json: { signed_in: true, via: "access", csrf, methods: { passkeys: 0, oidc: null, access: true } },
  }))
  await page.route("**/api/connections/*", route => route.fulfill({
    json: { platform: new URL(route.request().url()).pathname.split("/").at(-1), state: "not_configured", block: null, details: null },
  }))
  await page.route("**/api/modes", route => route.fulfill({
    json: { schema: { modes: ["off"], dm_modes: ["off"], actions: [], default: "off" }, spaces: [], dm: [] },
  }))
  await page.route("**/api/companions", route => route.fulfill({ json: { companions } }))
  await page.route("**/pairing/companion", route => {
    expect(route.request().method()).toBe("POST")
    expect(route.request().headers()["x-csrf-token"]).toBe(csrf)
    return route.fulfill({ json: { code, url, expires_in_s: 30 } })
  })
  await page.route("**/api/companions/device-1/revoke", route => {
    expect(route.request().method()).toBe("POST")
    expect(route.request().headers()["x-csrf-token"]).toBe(csrf)
    companions = companions.filter(device => device.id !== "device-1")
    return route.fulfill({ json: { ok: true } })
  })

  await page.clock.install()
  await page.setViewportSize({ width: 1200, height: 850 })
  await page.goto("/settings")
  const connections = page.getByRole("region", { name: "Connections" })
  const mac = connections.getByRole("listitem").filter({ hasText: "Studio Mac" })
  const laptop = connections.getByRole("listitem").filter({ hasText: "Workshop Laptop" })
  const pair = connections.getByRole("button", { name: "Pair a device" })
  const pairingCode = connections.getByRole("status", { name: "Pairing code" })
  await expect(mac.getByRole("img", { name: "darwin" })).toBeVisible()
  await expect(mac.getByText("Online", { exact: true })).toBeAttached()
  await expect(mac.getByText("notify", { exact: true })).toBeVisible()
  await expect(laptop.getByRole("img", { name: "linux" })).toBeVisible()
  await expect(laptop.getByText("Offline", { exact: true })).toBeAttached()
  await expect(pairingCode).toHaveCount(0)
  await connections.screenshot({ path: testInfo.outputPath("connections-desktop.png") })

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(mac.getByText("notify", { exact: true })).toBeVisible()
  await connections.evaluate(section => section.scrollIntoView({ block: "center", behavior: "instant" }))
  await connections.screenshot({ path: testInfo.outputPath("connections-mobile.png") })
  await pair.click()
  await expect(pairingCode).toHaveText(code)
  await expect(connections.getByText(url, { exact: true })).toHaveCount(0)
  await connections.getByRole("button", { name: "Close pairing code" }).click()
  await expect(pairingCode).toHaveCount(0)
  await pair.click()
  await expect(pairingCode).toBeVisible()
  await page.clock.fastForward(30_001)
  await expect(pairingCode).toHaveCount(0)

  await mac.getByRole("button", { name: "Disconnect Studio Mac" }).click()
  await page.getByRole("alertdialog", { name: "Disconnect Studio Mac?" }).getByRole("button", { name: "Disconnect", exact: true }).click()
  await expect(mac).toHaveCount(0)
  await expect(laptop).toBeVisible()
})

test("every tab responds to 20 touch taps, including after a route switch or while scrolled", async ({ page }) => {
  await page.goto("/")
  const nav = navigation(page)
  for (let index = 0; index < 20; index++) {
    const useLowerEdge = index % 2 === 0
    for (const [label, to] of [["Agents", "agents"], ["Tokens", "tokens"], ["Inbox", "inbox"]]) {
      await tap(page, nav.getByRole("link", { name: label, exact: true }), useLowerEdge)
      await expectTab(page, label, to)
    }
    await expect(page.getByText("Fixture task 1", { exact: true })).toBeVisible()
    if (index % 2 === 0) {
      await page.evaluate(() => window.scrollTo(0, 420))
      await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0)
    }
    await tap(page, nav.getByRole("button", { name: "More", exact: true }), useLowerEdge)
    await expect(menu(page)).toBeVisible()
    await tap(page, nav.getByRole("link", { name: "Overview", exact: true }), useLowerEdge)
    await expectTab(page, "Overview", "")
    await expect(menu(page)).toBeHidden()
  }
})

test("a touch during inbox fling momentum navigates on its first release", async ({ page, context }) => {
  await page.goto("/inbox")
  await expect(page.getByText("Fixture task 1", { exact: true })).toBeVisible()
  const nav = navigation(page)
  const box = await nav.getByRole("link", { name: "Agents", exact: true }).boundingBox()
  if (!box) throw new Error("Agents tab has no hit area")
  const input = await context.newCDPSession(page)
  await input.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: 180, y: 680 }] })
  for (const y of [625, 542, 429, 302, 165]) {
    await input.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: 180, y }] })
  }
  await input.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] })
  await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2)
  await input.detach()
  await expectTab(page, "Agents", "agents")
})

test("a pending lazy chunk does not hide the nav or override a newer tap", async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await page.route(/\/(?:src\/pages\/agents\.tsx|app-assets\/assets\/agents-[^/]+\.js)(?:\?.*)?$/, async route => {
    await pending
    await route.continue()
  })
  try {
    await page.goto("/")
    const nav = navigation(page)
    await tap(page, nav.getByRole("link", { name: "Agents", exact: true }))
    await expect(page).toHaveURL(/\/agents$/)
    await tap(page, nav.getByRole("link", { name: "Tokens", exact: true }))
    await expectTab(page, "Tokens", "tokens")
    release()
    await expect(page.getByRole("heading", { name: "Tokens", exact: true })).toBeVisible()
    await expectTab(page, "Tokens", "tokens")
  } finally {
    release()
  }
})

test("the sheet dismisses by row, scrim, Escape and a downward touch gesture", async ({ page, context }) => {
  await page.goto("/")
  await expect(page.getByRole("list", { name: "Projects" })).toBeVisible()
  const opener = navigation(page).getByRole("button", { name: "More", exact: true })
  const sheet = menu(page)
  await tap(page, opener)
  await waitForSheet(page)
  await expect(sheet.getByRole("link")).toHaveText(["Projects", "Memory", "Audit", "Settings", "About"])
  for (const item of await sheet.getByRole("link").all()) {
    expect((await item.boundingBox())?.height).toBeGreaterThanOrEqual(52)
  }
  await tap(page, sheet.getByRole("link", { name: "Settings", exact: true }))
  await expect(page).toHaveURL(/\/settings$/)
  await expect(sheet).toBeHidden()

  await tap(page, opener)
  await waitForSheet(page)
  await page.touchscreen.tap(30, 180)
  await expect(sheet).toBeHidden()
  await expect(page).toHaveURL(/\/settings$/)

  await tap(page, opener)
  await waitForSheet(page)
  await page.keyboard.press("Escape")
  await expect(sheet).toBeHidden()
  await expect(opener).toBeFocused()

  await tap(page, opener)
  await waitForSheet(page)
  const handle = sheet.getByRole("button", { name: "Close More" })
  const box = await handle.boundingBox()
  if (!box) throw new Error("Swipe handle has no hit area")
  const input = await context.newCDPSession(page)
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2
  await input.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] })
  await input.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y + 75 }] })
  await input.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] })
  await input.detach()
  await expect(sheet).toBeHidden()

  await tap(page, opener)
  await waitForSheet(page)
  await tap(page, sheet.getByRole("link", { name: "Projects", exact: true }))
  await expect(page).toHaveURL(/\/#projects$/)
  await expect(sheet).toBeHidden()
  await expect(page.locator("#projects")).toBeInViewport()
})
