import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"
import { expect, test, type Page } from "@playwright/test"
import type { BotIdentity, IdentityChange, IdentityPlatform } from "../src/lib/bot-identities"

const icon = "/avatars/swb.png"
const customIcon = `/avatars/bot-${"a".repeat(48)}.png`
const upload = fileURLToPath(new URL("../../src/switchboard/slack/avatars/dots.png", import.meta.url))
const bundled = fileURLToPath(new URL("../../src/switchboard/slack/avatars/swb.png", import.meta.url))
const permission = "https://api.slack.com/apps"
const postingLabel = "Post every agent as this bot"
const avatar = { file: null, state: "not_configured", url: null }
const identity = (name = "SwB", extra: Partial<BotIdentity> = {}): BotIdentity => ({
  name, icon_url: icon, can_edit: true, permissions_url: null, overridden: false, ...extra,
})
const connections = {
  discord: {
    block: "team-chat",
    details: { bot_name: "Release relay", owner_ids: ["1001"], avatar, invite_url: null, guilds: [
      { id: "1000", name: "Engineering", owner_id: "1001", space: "engineering", channels: [
        { id: "2001", name: "deployments", project: "operations", listening: true },
      ] },
    ] },
  },
  slack: { block: "studio-chat", details: { workspaces: [{ name: "Studio", connected: true, intake: "socket" }] } },
  telegram: {
    block: "dispatch-bot",
    details: { bot_name: "Dispatch relay", owner_ids: [], avatar, chats: [
      { id: "-1001000", title: "Dispatch", type: "supergroup", space: "dispatch", listen: "listening" },
    ] },
  },
}
const space = (id: string, name: string, identity: BotIdentity) => ({
  id, name, identity: { post_every_agent: false, ...identity },
})

async function fixture(page: Page, platform: IdentityPlatform) {
  const connection = connections[platform]
  const blocked = { can_edit: false, permissions_url: permission }
  const snapshot = {
    block: connection.block,
    platform,
    identity: platform === "slack" ? identity("SwB", blocked) : identity(),
    spaces: platform === "discord" ? [
      space("engineering", "Engineering", identity()),
      space("field-operations", "Field Operations", identity("Field relay", {
        icon_url: customIcon, overridden: true, post_every_agent: true,
      })),
    ] : platform === "slack" ? [
      space("studio", "Studio", identity("SwB", blocked)),
    ] : [space("dispatch", "Dispatch", identity("SwB", { can_edit: false }))],
  }
  const posts: IdentityChange[] = []
  await page.route("**/auth/session", route => route.fulfill({
    json: { signed_in: true, via: "access", csrf: "fixture-csrf", methods: { passkeys: 1, oidc: null, access: true } },
  }))
  await page.route("**/api/**", async route => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname === `/api/connections/${platform}`) return route.fulfill({ json: { ...connection, platform, state: "connected" } })
    if (pathname === "/api/modes") return route.fulfill({
      json: { schema: { modes: ["off"], dm_modes: ["off"], actions: [], default: "off" }, spaces: [], dm: [] },
    })
    if (pathname !== `/api/bot-identities/${connection.block}`) return route.fulfill({ status: 404, json: { error: "not_found" } })
    if (route.request().method() === "POST") {
      expect(route.request().headers()["x-csrf-token"]).toBe("fixture-csrf")
      const change = route.request().postDataJSON() as IdentityChange
      posts.push(change)
      if ("name" in change && change.name === "Rejected relay") return route.fulfill({ status: 422, json: { error: "identity_not_saved" } })
      const selected = snapshot.spaces.find(entry => entry.id === change.space)
      if (selected) {
        selected.identity = "reset" in change ? { ...snapshot.identity, post_every_agent: false, overridden: false } : {
          ...selected.identity,
          name: change.name ?? selected.identity.name,
          icon_url: change.icon ? customIcon : selected.identity.icon_url,
          post_every_agent: change.post_every_agent ?? selected.identity.post_every_agent,
          overridden: true,
        }
      } else {
        expect(change).not.toHaveProperty("post_every_agent")
        snapshot.identity = "reset" in change ? identity() : {
          ...snapshot.identity,
          name: change.name ?? snapshot.identity.name,
          icon_url: change.icon ? customIcon : snapshot.identity.icon_url,
          overridden: true,
        }
        snapshot.spaces.filter(entry => !entry.identity.overridden).forEach(entry => {
          entry.identity = { ...entry.identity, name: snapshot.identity.name, icon_url: snapshot.identity.icon_url }
        })
      }
    }
    return route.fulfill({ json: snapshot })
  })
  await page.route(`**${icon}`, route => route.fulfill({ path: bundled }))
  await page.route(`**${customIcon}`, route => route.fulfill({ path: upload }))
  await page.goto(`/connections/${platform}`)
  return posts
}

test("bot identity changes stage locally and save or reset the selected scope", async ({ page }) => {
  const posts = await fixture(page, "discord")
  const block = page.getByRole("form", { name: "Bot identity" })
  const engineering = page.getByRole("form", { name: "Engineering" })
  const field = page.getByRole("form", { name: "Field Operations" })
  await expect(block.getByRole("img", { name: "Current bot icon" })).toBeVisible()
  await expect(block.getByRole("button", { name: "Save" })).toBeDisabled()
  await expect(engineering.getByRole("button", { name: "Reset" })).toBeDisabled()
  await expect(field.getByRole("button", { name: "Reset" })).toBeEnabled()

  await block.getByRole("textbox", { name: "Name" }).fill("R")
  await block.getByRole("button", { name: "Save" }).click()
  await expect(block.getByRole("alert")).toHaveText("Enter a name with 2 to 32 characters.")
  await block.getByRole("textbox", { name: "Name" }).fill("Release relay")
  const chooser = page.waitForEvent("filechooser")
  await block.getByRole("button", { name: "Choose image" }).press("Enter")
  await (await chooser).setFiles(upload)
  const preview = block.getByRole("img", { name: "Image preview" })
  await expect(preview).toHaveAttribute("src", /^data:image\/png;base64,/)
  expect(posts).toHaveLength(0)
  await block.getByRole("button", { name: "Save" }).click()
  await expect(block.getByRole("img", { name: "Current bot icon" })).toHaveAttribute("src", customIcon)
  expect(posts[0]).toEqual({ name: "Release relay", icon: readFileSync(upload).toString("base64") })
  await expect(engineering.getByRole("textbox", { name: "Name" })).toHaveValue("Release relay")
  await expect(field.getByRole("textbox", { name: "Name" })).toHaveValue("Field relay")

  const name = engineering.getByRole("textbox", { name: "Name" })
  const posting = engineering.getByRole("switch", { name: postingLabel, exact: true })
  await posting.press("Space")
  await name.fill("Rejected relay")
  await name.press("Enter")
  await expect(engineering.getByRole("alert")).toHaveText("Save failed.")
  await expect(name).toHaveValue("Rejected relay")
  await expect(posting).toBeChecked()
  await name.fill("R")
  await name.press("Enter")
  await expect(engineering.getByRole("button", { name: "Reset" })).toBeEnabled()
  expect(posts.at(-1)).toEqual({ space: "engineering", name: "R", post_every_agent: true })
  await engineering.getByRole("button", { name: "Reset" }).click()
  await expect(name).toHaveValue("Release relay")
  await expect(posting).not.toBeChecked()
  expect(posts.at(-1)).toEqual({ space: "engineering", reset: true })
  await block.getByRole("button", { name: "Reset" }).click()
  await expect(name).toHaveValue("SwB")
  expect(posts.at(-1)).toEqual({ reset: true })
})

test("posting as the bot is staged and saved independently for each space", async ({ page }) => {
  const posts = await fixture(page, "discord")
  const block = page.getByRole("form", { name: "Bot identity" })
  const engineering = page.getByRole("form", { name: "Engineering" })
  const field = page.getByRole("form", { name: "Field Operations" })
  const posting = engineering.getByRole("switch", { name: postingLabel, exact: true })
  const savedField = field.getByRole("switch", { name: postingLabel, exact: true })
  const save = engineering.getByRole("button", { name: "Save" })
  await expect(block.getByRole("switch")).toHaveCount(0)
  await expect(posting).not.toBeChecked()
  await expect(savedField).toBeChecked()

  await posting.press("Space")
  await expect(save).toBeEnabled()
  expect(posts).toHaveLength(0)
  await posting.press("Space")
  await expect(save).toBeDisabled()
  await posting.press("Space")
  await save.click()
  await expect(save).toBeDisabled()
  expect(posts.at(-1)).toEqual({ space: "engineering", post_every_agent: true })
  await page.reload()
  await expect(posting).toBeChecked()

  await posting.press("Space")
  await save.click()
  await expect(save).toBeDisabled()
  expect(posts.at(-1)).toEqual({ space: "engineering", post_every_agent: false })
  await page.reload()
  await expect(posting).not.toBeChecked()
  await expect(savedField).toBeChecked()
  await field.getByRole("button", { name: "Reset" }).click()
  await expect(savedField).not.toBeChecked()
  expect(posts.at(-1)).toEqual({ space: "field-operations", reset: true })
})

test("the picker rejects unsupported, oversized and unreadable images", async ({ page }) => {
  const posts = await fixture(page, "discord")
  const block = page.getByRole("form", { name: "Bot identity" })
  const input = block.locator('input[type="file"]')
  await expect(input).toBeHidden()
  await input.setInputFiles({ name: "sample.svg", mimeType: "image/svg+xml", buffer: Buffer.from("<svg/>") })
  await expect(block.getByRole("alert")).toHaveText("Choose a PNG, JPEG or WebP image.")
  await input.setInputFiles({ name: "sample.png", mimeType: "image/png", buffer: Buffer.alloc(2 * 1024 * 1024 + 1) })
  await expect(block.getByRole("alert")).toHaveText("Choose an image up to 2 MB.")
  await input.setInputFiles({ name: "sample.png", mimeType: "image/png", buffer: Buffer.from("sample") })
  await expect(block.getByRole("alert")).toHaveText("Choose a valid image.")
  await expect(block.getByRole("button", { name: "Save" })).toBeDisabled()
  expect(posts).toHaveLength(0)
})

for (const theme of ["light", "dark"] as const) {
  test(`bot identity availability and connection renders in ${theme}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 950 })
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" })
    for (const platform of ["discord", "slack", "telegram"] as const) {
      await fixture(page, platform)
      const block = page.getByRole("form", { name: "Bot identity" })
      await expect(block.getByRole("img", { name: "Current bot icon" })).toBeVisible()
      if (platform === "slack") {
        await expect(block.getByRole("link", { name: "Slack permissions" })).toHaveAttribute("href", permission)
        await expect(block.getByRole("button", { name: "Choose image" })).toBeDisabled()
        await expect(block.getByRole("button", { name: "Save" })).toBeDisabled()
        await expect(page.getByRole("form", { name: "Studio" }).getByRole("switch", { name: postingLabel, exact: true })).toBeDisabled()
      }
      if (platform === "telegram") {
        const dispatch = page.getByRole("form", { name: "Dispatch" })
        await expect(block.getByRole("button", { name: "Choose image" })).toBeEnabled()
        await expect(dispatch.getByRole("button", { name: "Choose image" })).toBeDisabled()
        await expect(dispatch.getByRole("textbox", { name: "Name" })).toBeDisabled()
        const posting = dispatch.getByRole("switch", { name: postingLabel, exact: true })
        await expect(posting).toBeDisabled()
        await expect(posting).not.toBeChecked()
      }
      await expect(page.locator("html")).toHaveCSS("background-color", theme === "dark" ? "rgb(9, 9, 11)" : "rgb(255, 255, 255)")
      await page.screenshot({ path: testInfo.outputPath(`edit-bot-identity-${platform}-${theme}.png`), animations: "disabled" })
      await page.getByRole("region", { name: "Space identities" }).screenshot({
        path: testInfo.outputPath(`edit-bot-identity-${platform}-space-${theme}.png`), animations: "disabled",
      })
    }
  })
}
