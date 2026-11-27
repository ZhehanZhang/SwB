import { expect, test } from "@playwright/test"
import { fileURLToPath } from "node:url"

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
