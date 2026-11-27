import { expect, test, type Locator } from "@playwright/test"

async function expectAvatar(image: Locator, path: string) {
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
    await expectAvatar(page.getByRole("row").filter({ has: open }).locator("img").nth(1), inbox.conversations[0].target_avatar_url)
    await page.screenshot({ path: testInfo.outputPath("inbox-" + theme + ".png"), animations: "disabled" })

    await page.goto("/inbox/h_example_release")
    const detail = await (await page.request.get("/api/conversations/h_example_release")).json()
    const messages = page.getByRole("list", { name: "Messages" })
    const captain = messages.getByRole("listitem").filter({ hasText: "The checklist is ready to share." })
    await expect(captain).toContainText("Atlas")
    const avatarUrl = detail.items[1].from.avatar_url
    expect(avatarUrl).toMatch(/^\/avatars\/captain-atlas-/)
    await expectAvatar(captain.locator("img"), avatarUrl)
    const agent = messages.getByRole("listitem").filter({ hasText: "Please review the release checklist." })
    await expectAvatar(agent.locator("img"), "/avatars/claude-code.png")
    await page.screenshot({ path: testInfo.outputPath("conversation-" + theme + ".png"), animations: "disabled" })
  })
}
