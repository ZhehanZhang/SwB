import { expect, test } from "@playwright/test"
import { readFile } from "node:fs/promises"
import { fileURLToPath } from "node:url"

test("Sign-in explains an invalid link", async ({ page }) => {
  await page.setExtraHTTPHeaders({ "cf-access-jwt-assertion": "invalid" })
  await page.goto("/login#t=placeholder")
  await expect(page.getByText("Check your sign-in link.", { exact: true })).toBeVisible()
})

test("Tokens cancels and confirms revoking a paired key", async ({ page, request }, testInfo) => {
  await page.goto("/tokens")
  await page.getByText("Pairing code", { exact: true }).click()
  await page.getByLabel("Agent name").fill("codex")
  await page.getByRole("button", { name: "Create code" }).click()
  await expect(page).toHaveURL("/pairing/code")
  const code = await page.getByText(/^[A-Z2-9]{5}-[A-Z2-9]{5}$/).innerText()
  const paired = await request.post("/connect", { data: { code } })
  expect(paired.ok()).toBe(true)
  await page.getByRole("link", { name: "Back" }).click()
  await expect(page).toHaveURL("/tokens")

  const trigger = page.getByRole("button", { name: /revoke access key for codex/i })
  const dialog = page.getByRole("alertdialog", { name: "Revoke access key?" })
  await trigger.click()
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused()
  await page.keyboard.press("Escape")
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()

  await trigger.click()
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" })
    await expect(page.locator("html")).toHaveAttribute("data-theme", scheme)
    const path = testInfo.outputPath(`tokens-revoke-${scheme}.png`)
    await page.screenshot({ path, animations: "disabled" })
    await testInfo.attach(scheme, { path, contentType: "image/png" })
  }

  const revoked = page.waitForResponse((response) => response.request().method() === "POST"
    && /^\/api\/pairing\/keys\/[^/]+\/revoke$/.test(new URL(response.url()).pathname))
  await dialog.getByRole("button", { name: "Revoke" }).click()
  expect((await revoked).ok()).toBe(true)
  await expect(dialog).toBeHidden()
  await expect(trigger).toHaveCount(0)
})

test("Tokens shows and removes a paired key memory scope", async ({ page, request }, testInfo) => {
  await page.goto("/tokens")
  await page.getByText("Pairing code", { exact: true }).click()
  await page.getByLabel("Agent name").fill("memory-reviewer")
  await page.getByLabel("Project", { exact: true }).fill("operations")
  await page.getByRole("button", { name: "Create code" }).click()
  await expect(page).toHaveURL("/pairing/code")
  const code = await page.getByText(/^[A-Z2-9]{5}-[A-Z2-9]{5}$/).innerText()
  const paired = await request.post("/connect", { data: { code } })
  expect(paired.ok()).toBe(true)
  await page.getByRole("link", { name: "Back" }).click()

  const table = page.getByRole("table", { name: "Stored" })
  const row = table.getByRole("row").filter({ hasText: "memory-reviewer" })
  const project = row.getByRole("button", { name: "Remove operations from memory-reviewer" })
  const agent = row.getByRole("button", { name: "Remove Agent memory-reviewer from memory-reviewer" })
  await expect(project).toBeVisible()
  await expect(agent).toBeVisible()
  const path = testInfo.outputPath("memory-access.png")
  await table.screenshot({ path, animations: "disabled" })
  await testInfo.attach("Memory access", { path, contentType: "image/png" })
  await project.click()
  await expect(project).toHaveCount(0)
  await expect(agent).toBeVisible()
})

test("Agents saves a Slack persona avatar", async ({ page }) => {
  await page.goto("/classic?notice=avatar-refused")
  await expect(page).toHaveURL("/agents?notice=avatar-refused")
  const file = page.getByLabel("Review bot")
  await expect(file).toHaveAttribute("accept", "image/png")
  await file.setInputFiles(
    fileURLToPath(new URL("../../src/switchboard/slack/avatars/swb.png", import.meta.url)),
  )
  await page.getByRole("button", { name: "Save avatar" }).click()
  await expect(page).toHaveURL("/agents?notice=avatar-saved")
  await expect(page.getByRole("status").filter({ hasText: "Avatar saved" })).toBeVisible()
})

test("Settings shows the SwB commands", async ({ page }) => {
  await page.goto("/settings")
  const commands = page.getByRole("region", { name: "Commands", exact: true })
  await expect(commands.locator("code")).toHaveText(["swb up", "swb status", "swb log"])
  await expect(commands.getByRole("button", { name: "Copy swb log" })).toBeVisible()
})

test("Project copies the packaged coordinator prompt", async ({ page }, testInfo) => {
  const prompt = await readFile(new URL("../../src/switchboard/prompts/setup/coordinator.md", import.meta.url), "utf-8")
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: async (value: string) => { document.documentElement.dataset.testClipboard = value } },
    })
  })
  await page.goto("/projects/operations")
  const button = page.getByRole("button", { name: "Copy coordinator prompt" })
  await expect(button).toBeEnabled()
  await button.screenshot({ path: testInfo.outputPath("copy-coordinator.png") })
  await button.click()
  await expect(page.locator("html")).toHaveAttribute("data-test-clipboard", prompt)
  await expect(page.getByText("Coordinator prompt copied", { exact: true })).toBeVisible()
})

test("Memory shows human keys and opens notes by summary when a key is absent", async ({ page }, testInfo) => {
  await page.goto("/memory")
  const table = page.getByRole("table", { name: "Notes in general" })
  const keyed = table.getByRole("row").filter({ hasText: "Prepare the release." })
  const keyless = table.getByRole("row").filter({ hasText: "Review the schedule." })
  await expect(keyed.getByRole("cell").first()).toHaveText("release-plan")
  await expect(keyless.getByRole("cell").first()).toBeEmpty()
  await table.screenshot({ path: testInfo.outputPath("memory-keys.png"), animations: "disabled" })
  const open = keyless.getByRole("button", { name: "Review the schedule." })
  await open.click()
  const note = page.getByRole("dialog", { name: "Meeting notes" })
  await expect(note.getByText("Review the schedule.")).toBeVisible()
  await expect(note.getByText(/mem_/)).toHaveCount(0)
  await page.keyboard.press("Escape")
  await expect(open).toBeFocused()
})

test("Inbox shows more conversations and preserves the project filter through details", async ({ page }, testInfo) => {
  await page.goto("/inbox?project=operations")
  const conversations = page.getByRole("region", { name: "Conversations" })
  const rows = conversations.getByRole("row")
  const more = conversations.getByRole("button", { name: "More", exact: true })
  const older = conversations.getByRole("link", { name: "Conversation 1", exact: true })
  await expect(rows).toHaveCount(21)
  await expect(older).toHaveCount(0)
  await expect(page.getByRole("region", { name: "Needs you" }).getByText("Conversation 1", { exact: true })).toBeVisible()
  await expect(more).toBeVisible()
  await more.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath("inbox-more.png"), animations: "disabled" })
  await more.click()
  await expect(rows).toHaveCount(24)
  await expect(more).toHaveCount(0)
  await expect(conversations.getByRole("link", { name: "Conversation 23", exact: true })).toHaveCount(0)
  await expect(older).toHaveAttribute("href", "/inbox/h_history_1?project=operations")
  await older.click()
  await expect(page.getByRole("heading", { name: "Conversation 1" })).toBeVisible()
  await expect(page.getByText("The first conversation.")).toBeVisible()
  await page.getByRole("link", { name: "Conversations", exact: true }).click()
  await expect(page).toHaveURL("/inbox?project=operations")
  await expect(older).toBeVisible()

  await page.goto("/inbox/h_history_1")
  await expect(page.getByRole("heading", { name: "Conversation 1" })).toBeVisible()
  await expect(page.getByRole("link", { name: "Conversations", exact: true })).toHaveAttribute("href", "/inbox")
})
