import { expect, test, type Page } from "@playwright/test"

const modeButton = (page: Page, channel: string) => page.getByRole("button", { name: `Mode for ${channel}`, exact: true })
const targetButton = (page: Page, channel: string) => page.getByRole("button", { name: `Target for ${channel}`, exact: true })
async function choose(page: Page, label: string, option: string) {
  await page.getByRole("button", { name: label, exact: true }).click()
  await page.getByRole("menuitemradio", { name: option, exact: true }).click()
}
async function save(page: Page) {
  const editing = page.getByRole("group", { name: "Channel mode editing" })
  await editing.getByRole("button", { name: "Save", exact: true }).click()
  await expect(editing.getByRole("button", { name: "Saving…" })).toHaveCount(0)
}

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
  const mode = modeButton(page, "deployments")
  await expect(mode).toHaveText("Assist")
  const row = page.getByRole("row").filter({ has: mode })
  await expect(row).toContainText("From channel")
  const community = page.getByRole("row").filter({ has: modeButton(page, "community") })
  await expect(community.getByText("community", { exact: true })).toBeVisible()
  await choose(page, "Mode for deployments", "Watch")
  await save(page)
  await expect(row).toContainText("Override")
  await page.getByRole("button", { name: "Details for deployments" }).click()
  await expect(page.getByText("Pending messages")).toBeVisible()
  await expect(page.getByText("Next report")).toBeVisible()
  await expect(page.getByRole("cell").getByRole("link", { name: "Inbox" })).toHaveAttribute("href", "/inbox")
  await choose(page, "Mode for deployments", "Triage")
  await expect(page.getByRole("switch", { name: "Reply", exact: true })).toBeChecked()
  await expect(page.getByRole("switch", { name: "Label", exact: true })).toBeDisabled()
  await expect(page.getByRole("switch", { name: "Notify owner" })).toBeEnabled()
  await page.getByText("Notify owner", { exact: true }).filter({ visible: true }).click()
  await expect(page.getByRole("switch", { name: "Notify owner" })).toBeChecked()
  await save(page)

  await page.goto("/projects/operations")
  await page.getByRole("button", { name: "Channel modes", exact: true }).click()
  await expect(page.getByRole("table", { name: "Channel modes", exact: true })).toBeVisible()
  await expect(mode).toHaveText("Triage")
  await expect(modeButton(page, "community")).toHaveCount(0)
  await choose(page, "Mode for deployments", "Reset to inherit")
  await save(page)
  await expect(row).toContainText("From channel")
  await expect(mode).toHaveText("Assist")

  for (const [platform, channel, value] of [
    ["github", "example-org/example-repo", "Triage"],
    ["slack", "#planning", "Watch"],
  ]) {
    await page.goto(`/connections/${platform}`)
    if (platform === "github") await expect(page.getByText("Disconnected", { exact: true })).toBeVisible()
    await expect(page.getByRole("heading", { name: "Channel modes", exact: true })).toBeVisible()
    const table = page.getByRole("table", { name: "Channel modes", exact: true })
    await expect(table.getByRole("button", { name: `Mode for ${channel}` })).toHaveText(value)
  }
  await page.goto("/connections/telegram")
  await expect(modeButton(page, "-1001000")).toHaveText("Watch")
})

test("console mode and action edits keep the bound agent visible", async ({ page, request }) => {
  const session = await request.get("/auth/session")
  const { csrf } = await session.json()
  const headers = { Origin: new URL(session.url()).origin, "X-CSRF-Token": csrf }
  const repo = "example-org/example-repo"
  const placement = { block: "repo-events", space: "release", channel: repo }
  const address = "capy:captain:jam_EXAMPLEATLAS"
  const bound = await request.post("/api/modes", { headers, data: { ...placement, mode: "triage", target: address } })
  expect(bound.ok()).toBeTruthy()
  try {
    await page.goto("/connections/github")
    const mode = modeButton(page, repo)
    const row = page.getByRole("row").filter({ has: mode })
    await expect(targetButton(page, repo)).toHaveText("Atlas")
    await expect(targetButton(page, repo)).toHaveAttribute("title", address)
    await expect(row.getByText(address, { exact: true })).toHaveCount(0)
    await choose(page, `Target for ${repo}`, "Claude Code")
    await save(page)
    const agent = row.getByText("Claude Code", { exact: true })
    const avatar = targetButton(page, repo).locator("img")
    await expect(agent).toBeVisible()
    await expect(avatar).toBeVisible()
    await expect(row).not.toContainText("agent:claude-code")
    await choose(page, `Mode for ${repo}`, "Assist")
    await save(page)
    await expect(mode).toHaveText("Assist")
    await expect(agent).toBeVisible()
    await choose(page, `Mode for ${repo}`, "Triage")
    await page.getByRole("button", { name: "Details for " + repo }).click()
    const notify = page.getByRole("switch", { name: "Notify owner" })
    await page.getByText("Notify owner", { exact: true }).filter({ visible: true }).click()
    await expect(notify).toBeChecked()
    await save(page)
    await expect(agent).toBeVisible()
    await page.reload()
    await expect(mode).toHaveText("Triage")
    await expect(agent).toBeVisible()
    await expect(avatar).toBeVisible()
  } finally {
    const cleared = await request.post("/api/modes", { headers, data: { ...placement, mode: "inherit" } })
    expect(cleared.ok()).toBeTruthy()
  }
})

test("bulk edits stage mode and target, save, and reset to inherit", async ({ page, request }) => {
  await page.goto("/projects/operations?tab=modes")
  const repo = "example-org/example-repo"
  await expect(modeButton(page, "deployments")).toHaveText("Assist")
  await page.getByRole("checkbox", { name: "Select all channels" }).check()
  await expect(page.getByText("2 selected", { exact: true })).toBeVisible()
  await choose(page, "Mode for selected channels", "Watch")
  await choose(page, "Target for selected channels", "Atlas")
  await expect(modeButton(page, repo)).toHaveText("Watch")
  await expect(targetButton(page, "deployments")).toHaveText("Atlas")
  const before = await (await request.get("/api/modes")).json()
  expect(before.spaces.find((row: { name: string }) => row.name === "deployments").mode).toBe("assist")
  await page.getByRole("button", { name: "Cancel" }).click()
  await expect(modeButton(page, repo)).toHaveText("Triage")
  await choose(page, "Mode for selected channels", "Watch")
  await choose(page, "Target for selected channels", "Atlas")
  await save(page)
  await page.reload()
  await expect(modeButton(page, repo)).toHaveText("Watch")
  await expect(targetButton(page, "deployments")).toHaveText("Atlas")
  await page.getByRole("checkbox", { name: "Select all channels" }).check()
  await choose(page, "Mode for selected channels", "Reset to inherit")
  await save(page)
  await expect(modeButton(page, "deployments")).toHaveText("Assist")
  await expect(modeButton(page, repo)).toHaveText("Triage")
})

test("a failed save keeps its draft and offers a plain inline error", async ({ page }) => {
  await page.goto("/projects/operations?tab=modes")
  const route = "**/api/modes"
  await page.route(route, request => request.request().method() === "POST"
    ? request.fulfill({ status: 400, json: { error: "mode_not_saved" } }) : request.continue())
  await choose(page, "Mode for deployments", "Off")
  await save(page)
  const message = page.getByRole("alert").filter({ hasText: "Check this channel’s mode and target, then try again." })
  await expect(message).toBeVisible()
  await expect(modeButton(page, "deployments")).toHaveText("Off")
  await page.getByRole("button", { name: "Cancel" }).click()
  await expect(message).toHaveCount(0)
  await expect(modeButton(page, "deployments")).toHaveText("Assist")
})

test("triage switches link to the affected GitHub App installation", async ({ page }) => {
  const url = "https://github.com/organizations/example-org/settings/installations/123"
  await page.route("**/api/modes", async route => {
    const response = await route.fetch()
    const body = await response.json()
    body.github.installations = [{ permissions: { issues: "read" }, repositories: ["example-org/example-repo"], permissions_url: url }]
    await route.fulfill({ response, json: body })
  })
  await page.goto("/projects/operations?tab=modes")
  await page.getByRole("button", { name: "Details for example-org/example-repo" }).click()
  await expect(page.getByRole("switch", { name: "Label", exact: true })).toBeDisabled()
  await expect(page.getByRole("switch", { name: "Notify owner" })).toBeEnabled()
  await expect(page.getByRole("link", { name: "Review permissions" })).toHaveAttribute("href", url)
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

  test("each mobile channel row keeps its mode separate from its labelled disclosure", async ({ page }) => {
    for (const [platform, name, source] of [
      ["discord", "deployments", "From channel"],
      ["slack", "#planning", "From channel"],
      ["telegram", "-1001000", "From space"],
      ["github", "example-org/example-repo", "From space"],
    ]) {
      await page.goto(`/connections/${platform}`)
      const mode = modeButton(page, name)
      const details = page.getByRole("button", { name: `Details for ${name}`, exact: true })
      await expect(mode).toBeVisible()
      const row = page.getByRole("row").filter({ has: mode })
      const nameBox = await row.getByText(name, { exact: true }).filter({ visible: true }).boundingBox()
      const modeBox = await mode.boundingBox()
      const detailsBox = await details.boundingBox()
      expect(nameBox && modeBox && detailsBox).toBeTruthy()
      if (!nameBox || !modeBox || !detailsBox) continue
      expect(nameBox.x).toBeLessThan(modeBox.x)
      expect(detailsBox.x + detailsBox.width).toBeLessThan(390)
      await expect(details).toHaveText("Details")
      await details.tap()
      await expect(details).toHaveAttribute("aria-expanded", "true")
      await expect(page.getByRole("group", { name: "Triage actions" })).toBeVisible()
      await choose(page, `Mode for ${name}`, "Work")
      await save(page)
      await expect(mode).toHaveText("Work")
      await expect(details).toHaveAttribute("aria-expanded", "true")
      await choose(page, `Mode for ${name}`, "Reset to inherit")
      await save(page)
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
