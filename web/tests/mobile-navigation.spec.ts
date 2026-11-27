import { expect, test, type Locator, type Page } from "@playwright/test"

const board = {
  runtime: { online: true, mcp_url: null, host: "fixture", version: null, uptime_seconds: 0 },
  projects: [{ slug: "ops", scope: "project:ops", name: "Operations", archived: false, created_at: null,
    turn_cap: null, memory_count: 0, open_count: 25, sources: [], agents: [] }],
  unassigned: [],
}
const inbox = {
  pending_writes: [], turn_cap: null, pauses: [],
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
