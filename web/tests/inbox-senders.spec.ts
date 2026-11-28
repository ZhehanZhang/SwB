import { expect, test, type Locator } from "@playwright/test"

test.use({ viewport: { width: 1536, height: 1000 } })

async function expectAvatar(image: Locator, path: string | RegExp) {
  await expect(image).toHaveAttribute("src", path)
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true)
  await expect(image).toHaveCSS("filter", "none")
}

for (const theme of ["light", "dark"] as const) {
  test("Inbox renders sender identities in " + theme, async ({ page }, testInfo) => {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" })
    const response = await page.goto("/inbox")
    expect(response?.headers()["content-security-policy"]).toMatch(/img-src 'self'(?: data:)?;/)
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme)

    const inbox = await (await page.request.get("/api/inbox")).json()
    const [request] = inbox.pending_writes
    const section = page.getByRole("region", { name: "Needs you" })
    const approval = section.getByRole("listitem").filter({ hasText: "Claude Code" })
    await expect(approval).toContainText("The release checklist is ready for review.")
    await expect(approval).not.toContainText(request.key_id)
    await expectAvatar(approval.locator("img"), "/avatars/claude-code.png")
    const open = page.getByRole("link", { name: "Release preparation" })
    await expect(open).toHaveAttribute("href", "/inbox/h_example_release")
    expect(inbox.conversations[0].target_avatar_url).toMatch(/^\/avatars\/captain-atlas-/)
    await expectAvatar(page.getByRole("row").filter({ has: open }).locator("img").nth(1), /\/app-assets\/assets\/captain-[\w-]+\.webp$/)
    const watch = page.getByRole("row").filter({ hasText: "GitHub watch" })
    await expect(watch).toContainText("Notified")
    const telegram = page.getByRole("row").filter({ hasText: "Schedule confirmed for Thursday." })
    await expect(telegram).toContainText("dispatch › Release coordination")
    await expect(telegram.getByRole("img", { name: "Unassigned" })).toBeVisible()
    await expect(telegram).not.toContainText("dispatch:-1001000:5")
    const slack = page.getByRole("row").filter({ hasText: "Draft the agenda for our planning meeting." })
    await expect(slack).toContainText("Jamie")
    await expect(slack).toContainText("Grok Bot")
    await expect(slack).not.toContainText("Slack studio")
    const dots = page.getByRole("row").filter({ hasText: "Summarize the open review items." })
    await expect(dots).toContainText("#planning")
    const coding = page.getByRole("row").filter({ hasText: "Review the build output" })
    await expect(coding).toContainText("OpenCode")
    await expect(coding).toContainText("Goose")
    await expectAvatar(coding.locator("img:visible"), new RegExp(`/app-assets/assets/opencode${theme === "dark" ? "-dark" : ""}-[\\w-]+\\.svg$`))
    await page.screenshot({ path: testInfo.outputPath("inbox-" + theme + ".png"), animations: "disabled" })

    await watch.getByRole("link").click()
    const digest = page.getByRole("region", { name: "Watch digest: example-org/example-repo" })
    await expect(digest.getByText("Notified", { exact: true }).first()).toBeVisible()
    await expect(digest.getByText("telegram:dispatch")).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath("inbox-split-" + theme + ".png"), animations: "disabled" })
    await open.click()
    const detail = await (await page.request.get("/api/conversations/h_example_release")).json()
    const messages = page.getByRole("list", { name: "Messages" })
    const captain = messages.getByRole("listitem").filter({ hasText: "The checklist is ready to share." })
    await expect(captain).toContainText("Atlas")
    const avatarUrl = detail.items[1].from.avatar_url
    expect(avatarUrl).toMatch(/^\/avatars\/captain-atlas-/)
    await expectAvatar(captain.locator("img"), /\/app-assets\/assets\/captain-[\w-]+\.webp$/)
    const agent = messages.getByRole("listitem").filter({ hasText: "Please review the release checklist." })
    await expectAvatar(agent.locator("img"), "/avatars/claude-code.png")
    await page.screenshot({ path: testInfo.outputPath("conversation-" + theme + ".png"), animations: "disabled" })
  })
}

test("Inbox actions provide a per-row undo and a multi-select save", async ({ page }, testInfo) => {
  const actionRequests: string[] = []
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/inbox/actions") actionRequests.push(request.method())
  })
  await page.goto("/inbox")
  const list = page.getByRole("region", { name: "Conversations" })
  const dots = list.getByRole("row").filter({ hasText: "Summarize the open review items." })
  const handoff = list.getByRole("row").filter({ hasText: "Add the release date to the changelog." })
  await dots.getByRole("button", { name: "Actions for Summarize the open review items." }).click()
  await page.getByRole("menuitem", { name: "Mark read" }).click()
  await page.getByRole("button", { name: "Undo" }).click()
  expect(actionRequests).toEqual([])
  await list.getByRole("button", { name: "Select", exact: true }).click()
  await dots.getByRole("checkbox").check()
  await handoff.getByRole("checkbox").check()
  await expect(list.getByRole("button", { name: "Mark read" })).toBeEnabled()
  await page.screenshot({ path: testInfo.outputPath("inbox-actions.png"), animations: "disabled" })
  await page.emulateMedia({ colorScheme: "dark" })
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark")
  await page.screenshot({ path: testInfo.outputPath("inbox-actions-dark.png"), animations: "disabled" })
  const saved = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/inbox/actions")
  await list.getByRole("button", { name: "Mark read" }).click()
  await page.locator("h1").hover()
  const response = await saved
  expect(response.ok()).toBe(true)
  expect((await response.json()).updated.sort()).toEqual(["h_example_dots", "h_example_handoff"])
})
