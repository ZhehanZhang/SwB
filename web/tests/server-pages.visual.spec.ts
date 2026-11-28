import { join } from "node:path"
import { expect, test } from "@playwright/test"

const consentHeader = { "cf-access-jwt-assertion": "server-pages-fixture" }
const before = process.env.SWB_SERVER_PAGES_CAPTURE_BASELINE === "1"
const pages = [
  { name: "oauth-consent", url: "/fixture/consent", owner: true },
  { name: "oauth-consent-unknown", url: "/fixture/consent-unknown", owner: true },
  { name: "oauth-error", url: "/fixture/oauth-error", status: 400 },
  { name: "sign-in", url: "/fixture/sign-in" },
  { name: "passkey", url: "/fixture/passkey" },
  { name: "passkey-failed", url: "/fixture/passkey-failed" },
  { name: "setup-code", url: "/fixture/setup" },
  { name: "login-link", url: "/login" },
  { name: "pairing-code", url: "/fixture/pairing" },
  { name: "pairing-error", url: "/fixture/pairing-error", status: 400 },
  { name: "token-claim", url: "/fixture/token" },
  { name: "routine-claim", url: "/fixture/routine" },
  { name: "group-claim", url: "/fixture/group" },
  { name: "owner-key-claim", url: "/fixture/owner-key" },
  { name: "claim-saved", url: "/fixture/saved" },
  { name: "claim-expired", url: "/fixture/expired", status: before ? 200 : 410 },
  { name: "slack-approval-unavailable", url: "/fixture/slack-approval", owner: true, status: 403 },
  { name: "console-build-required", url: "/", owner: true, status: 503 },
  { name: "404", url: "/fixture/missing", status: 404 },
  { name: "500", url: "/fixture/server-error", status: 500 },
]
const widths = [375, 768, 1024, 1440, 1920]
const themes = ["light", "dark"] as const
const auditDir = process.env.SWB_SERVER_PAGES_RENDER_DIR

for (const width of widths) {
  for (const theme of themes) {
    test(`server pages at ${width}px in ${theme} mode`, async ({ page }, info) => {
      await page.setViewportSize({ width, height: width === 375 ? 812 : 900 })
      await page.emulateMedia({ colorScheme: theme })
      for (const fixture of pages) {
        await test.step(fixture.name, async () => {
          await page.setExtraHTTPHeaders({ accept: "text/html", ...(fixture.owner ? consentHeader : {}) })
          const response = await page.goto(fixture.url)
          expect(response?.status()).toBe(fixture.status ?? 200)
          await expect(page.locator("body")).toBeVisible()
          if (!before) {
            const heading = page.locator("main h1")
            await expect(heading).toBeVisible()
            await expect(heading).toHaveCSS("font-family", /Inter Variable/)
            await expect(heading).toHaveCSS("font-size", "20px")
            await expect(heading).toHaveCSS("font-weight", "600")
            const action = page.locator(".actions > .button").first()
            if (await action.count()) {
              await expect(action).toBeVisible()
              expect((await action.boundingBox())?.x).toBe((await heading.boundingBox())?.x)
            }
            await expect(page.locator("html")).toHaveAttribute("data-theme", theme)
            await expect(page.locator("html")).toHaveCSS("background-color",
              theme === "dark" ? "rgb(9, 9, 11)" : "rgb(255, 255, 255)")
            expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width)
            if (fixture.name === "oauth-consent-unknown") {
              await expect(page.locator(".mailbox code")).toHaveText(
                "agent:northwind-procurement-assistant-staging-environment-" + "x".repeat(40))
            }
          }
          const name = `${fixture.name}-${width}-${theme}-${before ? "before" : "after"}.png`
          await page.screenshot({ path: auditDir ? join(auditDir, name) : info.outputPath(name), fullPage: true, animations: "disabled" })
        })
      }
    })
  }
}

test("OAuth consent supports both keyboard decisions", async ({ page }) => {
  test.skip(before, "Capture baseline screenshots only")
  const callbacks: string[] = []
  await page.route(url => url.pathname === "/authorize", async route => {
    if (route.request().method() !== "POST") return route.continue()
    const response = await route.fetch({ maxRedirects: 0 })
    callbacks.push(response.headers().location)
    return route.fulfill({ body: "Fixture callback" })
  })
  await page.setExtraHTTPHeaders(consentHeader)
  await page.goto("/fixture/consent")
  const approve = page.getByRole("button", { name: "Approve" })
  const deny = page.getByRole("button", { name: "Deny" })
  await expect(approve).not.toBeFocused()
  await expect(approve).toHaveCSS("outline-style", "none")
  await expect(approve).toHaveCSS("width", (await deny.boundingBox())?.width + "px")
  await page.getByRole("heading").click()
  await expect(approve).toHaveCSS("outline-style", "none")
  await page.keyboard.press("Enter")
  await expect.poll(() => callbacks.length).toBeGreaterThan(0)
  expect(new URL(callbacks[0]).searchParams.get("code")).toBeTruthy()
  expect(new URL(callbacks[0]).searchParams.get("state")).toBe("fixture-state")

  await page.goto("/fixture/consent")
  await page.keyboard.press("Tab")
  await expect(approve).toBeFocused()
  await expect(approve).toHaveCSS("outline-style", "solid")
  await page.keyboard.press("Tab")
  await expect(deny).toBeFocused()
  await expect(deny).toHaveCSS("outline-style", "solid")
  await page.keyboard.press("Enter")
  await expect.poll(() => callbacks.length).toBeGreaterThan(1)
  expect(new URL(callbacks[1]).searchParams.get("error")).toBe("access_denied")
  expect(new URL(callbacks[1]).searchParams.get("state")).toBe("fixture-state")
})

test("a failed passkey attempt explains itself and allows another try", async ({ page }) => {
  test.skip(before, "Capture baseline screenshots only")
  await page.route("**/auth/passkey/login/options", route => route.fulfill({
    json: { publicKey: { challenge: "Zml4dHVyZQ" } },
  }))
  await page.addInitScript(() => {
    navigator.credentials.get = () => Promise.reject(new DOMException("Fixture refusal", "NotAllowedError"))
  })
  await page.goto("/fixture/passkey")
  const button = page.getByRole("button", { name: "Sign in with a passkey" })
  await button.click()
  await expect(page.locator("#login-error")).toHaveText("Try your passkey again.")
  await expect(button).toBeEnabled()
})

test("a stalled passkey request times out and allows another try", async ({ page }) => {
  test.skip(before, "Capture baseline screenshots only")
  await page.addInitScript(() => {
    const timeout = AbortSignal.timeout.bind(AbortSignal)
    AbortSignal.timeout = () => timeout(50)
    const fetch = window.fetch.bind(window)
    window.fetch = (url, options) => {
      if (url !== "/auth/passkey/login/options") return fetch(url, options)
      return new Promise((_resolve, reject) => options?.signal?.addEventListener("abort", () => reject(options.signal?.reason), { once: true }))
    }
  })
  await page.goto("/fixture/passkey")
  const button = page.getByRole("button", { name: "Sign in with a passkey" })
  await button.click()
  await expect(page.locator("#login-error")).toHaveText("Try your passkey again.")
  await expect(button).toBeEnabled()
})
