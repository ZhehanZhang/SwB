import { expect, test } from "@playwright/test"

test("settings shows the saved passkey and its last used date", async ({ page }, testInfo) => {
  await page.goto("/settings")
  const passkeys = page.getByRole("region", { name: "Sign-in" })
  await expect(passkeys.getByText("Fixture passkey", { exact: true })).toBeVisible()
  await expect(passkeys.getByText("Last used Jan 2, 2026", { exact: true })).toBeVisible()
  await passkeys.screenshot({ path: testInfo.outputPath("settings-passkey.png"), animations: "disabled" })
})

test("a direct agent URL opens a mailbox absent from the registered list", async ({ page }, testInfo) => {
  await page.goto("/agents/alpha")
  const agent = page.getByRole("dialog", { name: "alpha" })
  await expect(agent.getByRole("heading", { name: "alpha", exact: true })).toBeVisible()
  await expect(agent.getByText("Mailbox", { exact: true })).toBeVisible()
  await agent.screenshot({ path: testInfo.outputPath("agent-mailbox.png"), animations: "disabled" })
})

test("connection and project controls use the owner mode API", async ({ page, context }) => {
  const unsigned = await context.request.get("/api/modes", { headers: { "cf-access-jwt-assertion": "invalid" } })
  expect(unsigned.status()).toBe(401)
  expect(unsigned.headers()["cache-control"]).toBe("no-store")

  const response = page.waitForResponse(value => new URL(value.url()).pathname === "/api/connections/github")
  await page.goto("/settings")
  const status = await response
  expect(status.headers()["cache-control"]).toBe("no-store")
  expect((await status.json()).state).toBe("disconnected")
  const github = page.getByRole("link", { name: /GitHub/ })
  await expect(github).toBeVisible()
  await expect(github).toContainText("Disconnected")
  await page.getByRole("link", { name: /Discord/ }).click()
  const mode = page.getByRole("combobox", { name: "Mode for deployments" })
  await expect(mode).toHaveValue("assist")
  const row = page.getByRole("row").filter({ has: mode })
  await expect(row).toContainText("From channel")
  const community = page.getByRole("row").filter({ has: page.getByRole("combobox", { name: "Mode for community" }) })
  await expect(community.getByRole("cell").nth(1)).toHaveText("community")
  await mode.selectOption("watch")
  await expect(row).toContainText("Override")
  await page.getByRole("button", { name: "Details for deployments" }).click()
  await expect(page.getByText("Pending messages")).toBeVisible()
  await expect(page.getByText("Next report")).toBeVisible()
  await expect(page.getByRole("cell").getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/inbox")
  await mode.selectOption("triage")
  await expect(page.getByText("Choose a triage agent.")).toBeVisible()
  await expect(page.getByRole("checkbox", { name: "Reply", exact: true })).toBeChecked()
  await expect(page.getByRole("checkbox", { name: "Label", exact: true })).toBeDisabled()
  await expect(page.getByRole("checkbox", { name: "Notify owner" })).toBeEnabled()
  await expect(page.getByRole("group", { name: "Triage actions" }).getByText("Issue actions require GitHub write access.")).toBeVisible()
  await page.getByRole("checkbox", { name: "Notify owner" }).click()
  await expect(page.getByRole("checkbox", { name: "Notify owner" })).toBeChecked()

  await page.goto("/projects/operations")
  await page.getByRole("button", { name: "Channel modes", exact: true }).click()
  await expect(page.getByRole("table", { name: "Channel modes", exact: true })).toBeVisible()
  await expect(mode).toHaveValue("triage")
  await expect(page.getByRole("combobox", { name: "Mode for community" })).toHaveCount(0)
  await mode.selectOption("inherit")
  await expect(row).toContainText("From channel")
  await expect(mode).toHaveValue("assist")

  for (const [platform, channel, value] of [
    ["github", "example-org/example-repo", "triage"],
    ["slack", "#planning", "watch"],
  ]) {
    await page.goto(`/connections/${platform}`)
    if (platform === "github") await expect(page.getByText("Disconnected", { exact: true })).toBeVisible()
    await expect(page.getByRole("heading", { name: "Channel modes", exact: true })).toBeVisible()
    const table = page.getByRole("table", { name: "Channel modes", exact: true })
    await expect(table.getByRole("combobox", { name: `Mode for ${channel}` })).toHaveValue(value)
  }
  await page.goto("/connections/telegram")
  await expect(page.getByRole("combobox", { name: "Mode for -1001000" })).toHaveValue("watch")
})

test("console mode and action edits keep the bound agent visible", async ({ page, request }) => {
  const session = await request.get("/auth/session")
  const { csrf } = await session.json()
  const headers = { Origin: new URL(session.url()).origin, "X-CSRF-Token": csrf }
  const repo = "example-org/example-repo"
  const placement = { block: "repo-events", space: "release", channel: repo }
  const address = "capy:captain:jam_EXAMPLEATLAS"
  const target = "agent:claude-code"
  const bound = await request.post("/api/modes", { headers, data: { ...placement, mode: "triage", target: address } })
  expect(bound.ok()).toBeTruthy()
  try {
    await page.goto("/connections/github")
    const mode = page.getByRole("combobox", { name: "Mode for " + repo })
    const row = page.getByRole("row").filter({ has: mode })
    await expect(row.getByText(address, { exact: true }).filter({ visible: true })).toHaveClass(/font-mono/)
    expect((await request.post("/api/modes", { headers, data: { ...placement, mode: "triage", target } })).ok()).toBeTruthy()
    await page.reload()
    const agent = row.getByText("Claude Code", { exact: true }).filter({ visible: true })
    const avatar = row.locator('img[src="/avatars/claude-code.png"]').filter({ visible: true })
    await expect(agent).toBeVisible()
    await expect(avatar).toBeVisible()
    await expect(row).not.toContainText(target)
    await mode.selectOption("assist")
    await expect(mode).toHaveValue("assist")
    await expect(agent).toBeVisible()
    await mode.selectOption("triage")
    await page.getByRole("button", { name: "Details for " + repo }).click()
    const notify = page.getByRole("checkbox", { name: "Notify owner" })
    await notify.click()
    await expect(notify).toBeChecked()
    await expect(agent).toBeVisible()
    await page.reload()
    await expect(mode).toHaveValue("triage")
    await expect(agent).toBeVisible()
    await expect(avatar).toBeVisible()
  } finally {
    const cleared = await request.post("/api/modes", { headers, data: { ...placement, mode: "inherit" } })
    expect(cleared.ok()).toBeTruthy()
  }
})

test("claims and login share the console theme and follow the system by default", async ({ page, browser }) => {
  const paths = ["/fixture/token", "/fixture/credentials", "/fixture/login"]
  await page.setExtraHTTPHeaders({ "cf-access-jwt-assertion": "invalid" })
  await page.emulateMedia({ colorScheme: "dark" })
  for (const path of paths) {
    const response = await page.goto(path)
    expect(response?.ok()).toBe(true)
    expect(response?.headers()["content-security-policy"]).toContain("script-src 'self'")
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(9, 9, 11)")
  }
  await page.evaluate(() => localStorage.setItem("swb-theme", "light"))
  for (const path of paths) {
    await page.goto(path)
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(255, 255, 255)")
  }
  await page.emulateMedia({ colorScheme: "light" })
  await page.evaluate(() => localStorage.setItem("swb-theme", "dark"))
  for (const path of paths) {
    await page.goto(path)
    await expect(page.locator("html")).toHaveCSS("background-color", "rgb(9, 9, 11)")
  }

  const fallback = await browser.newPage({ baseURL: new URL(page.url()).origin, javaScriptEnabled: false, colorScheme: "dark" })
  try {
    await fallback.goto("/fixture/login")
    await expect(fallback.locator("html")).toHaveCSS("background-color", "rgb(9, 9, 11)")
  } finally {
    await fallback.close()
  }
})

test("dropped webhooks show the reason in the audit log", async ({ page }) => {
  await page.goto("/audit")
  const row = page.getByRole("row").filter({ hasText: "Dropped webhook for example-org/example-repo" })
  await expect(row.getByRole("cell", { name: "channel mode off", exact: true })).toBeVisible()
})

test("dropped chat wakes show the reason in Messages", async ({ page }) => {
  await page.goto("/audit")
  await page.getByRole("tab", { name: "Messages" }).click()
  const row = page.getByRole("row").filter({ hasText: "Dropped chat wake in engineering/2002" })
  await expect(row.getByRole("cell", { name: "triage needs an agent", exact: true })).toBeVisible()
})

test("agent tiles and claim pages display bundled avatars", async ({ page }) => {
  await page.goto("/agents")
  for (const name of ["claude-code", "codex", "chatgpt", "gemini"]) {
    const avatar = page.locator(`img[src="/avatars/${name}.png"]`).first()
    await expect(avatar).toBeVisible()
    await expect.poll(() => avatar.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0)
  }

  for (const [path, name] of [["/tokens", "capy"], ["/fixture/gemini", "gemini"]]) {
    await page.goto(path)
    const avatar = page.locator(`img[src="/avatars/${name}.png"]`).first()
    await expect(avatar).toBeVisible()
    await expect.poll(() => avatar.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(512)
  }
})

test.describe("mobile connections", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test("each channel row keeps its mode separate from the full-width disclosure", async ({ page }) => {
    for (const [platform, name, source] of [
      ["discord", "deployments", "From channel"],
      ["slack", "#planning", "From channel"],
      ["telegram", "-1001000", "From space"],
      ["github", "example-org/example-repo", "From space"],
    ]) {
      await page.goto(`/connections/${platform}`)
      const mode = page.getByRole("combobox", { name: `Mode for ${name}`, exact: true })
      const details = page.getByRole("button", { name: `Details for ${name}`, exact: true })
      await expect(mode).toBeVisible()
      const row = page.getByRole("row").filter({ has: mode })
      const nameBox = await row.getByText(name, { exact: true }).filter({ visible: true }).boundingBox()
      const modeBox = await mode.boundingBox()
      const sourceBox = await row.getByText(source, { exact: true }).filter({ visible: true }).boundingBox()
      const detailsBox = await details.boundingBox()
      expect(nameBox && modeBox && sourceBox && detailsBox).toBeTruthy()
      if (!nameBox || !modeBox || !sourceBox || !detailsBox) continue
      expect(nameBox.x).toBeLessThan(modeBox.x)
      expect(Math.abs(nameBox.y + nameBox.height / 2 - modeBox.y - modeBox.height / 2)).toBeLessThan(3)
      expect(sourceBox.y).toBeGreaterThanOrEqual(modeBox.y + modeBox.height)
      expect(detailsBox.width).toBeGreaterThan(320)
      await details.scrollIntoViewIfNeeded()
      const sourceAfterScroll = await row.getByText(source, { exact: true }).filter({ visible: true }).boundingBox()
      if (!sourceAfterScroll) throw new Error("Mode source has no tap area")
      await page.touchscreen.tap(sourceAfterScroll.x + 2, sourceAfterScroll.y + sourceAfterScroll.height / 2)
      await expect(details).toHaveAttribute("aria-expanded", "true")
      await expect(page.getByRole("group", { name: "Triage actions" })).toBeVisible()
      await mode.selectOption("work")
      await expect(mode).toHaveValue("work")
      await expect(details).toHaveAttribute("aria-expanded", "true")
      await mode.selectOption("inherit")
      await expect(row.getByText(source, { exact: true }).filter({ visible: true })).toBeVisible()

      if (platform === "discord" || platform === "telegram") {
        await expect(page.getByRole("heading", { name: "Bot", exact: true })).toHaveCount(0)
        await expect(page.getByText("Disconnected", { exact: true })).toBeVisible()
      }
      if (platform === "slack") {
        const workspaceStatus = page.getByRole("table", { name: "Slack workspaces" }).getByText("Disconnected", { exact: true })
        expect((await workspaceStatus.boundingBox())?.height).toBeLessThan(24)
      }
    }
  })
})
