import { expect, test, type Page, type TestInfo } from "@playwright/test"

const schemes = ["light", "dark"] as const
const preferences = [...schemes, "system"] as const
type Scheme = typeof schemes[number]
type Preference = typeof preferences[number]

const colors = {
  light: { hex: "#ffffff", rgb: "rgb(255, 255, 255)" },
  dark: { hex: "#09090b", rgb: "rgb(9, 9, 11)" },
}

function readTheme() {
  const root = document.documentElement
  const style = getComputedStyle(root)
  return {
    active: root.dataset.theme,
    preference: root.dataset.themeMode,
    darkClass: root.classList.contains("dark"),
    inlineScheme: root.style.colorScheme,
    inlineBackground: root.style.backgroundColor,
    computedScheme: style.colorScheme,
    computedBackground: style.backgroundColor,
    colorSchemeMeta: Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="color-scheme"]'), meta => meta.content),
    themeColorMeta: Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'), meta => ({
      media: meta.media, content: meta.content, matches: matchMedia(meta.media).matches,
    })).sort((a, b) => b.media.localeCompare(a.media)),
  }
}

function expectedTheme(preference: Preference, os: Scheme) {
  const active = preference === "system" ? os : preference
  return {
    active,
    preference,
    darkClass: active === "dark",
    inlineScheme: active,
    inlineBackground: colors[active].rgb,
    computedScheme: active,
    computedBackground: colors[active].rgb,
    colorSchemeMeta: [preference === "system" ? "light dark" : active],
    themeColorMeta: schemes.map(scheme => ({
      media: `(prefers-color-scheme: ${scheme})`,
      content: colors[preference === "system" ? scheme : active].hex,
      matches: os === scheme,
    })),
  }
}

async function withBootstrapOnly(page: Page) {
  await page.route(/\/app-assets\/assets\/.*\.js$/, route =>
    new URL(route.request().url()).pathname === "/app-assets/assets/theme.js" ? route.continue() : route.abort())
}

async function captureDOMContentLoaded(page: Page, testInfo: TestInfo) {
  const path = testInfo.outputPath("domcontentloaded.png")
  await page.screenshot({ path })
  await testInfo.attach("DOMContentLoaded", { path, contentType: "image/png" })
}

for (const os of schemes) {
  for (const preference of preferences) {
    test(`first paint with ${os} OS and stored ${preference}`, async ({ page }, testInfo) => {
      await page.emulateMedia({ colorScheme: os })
      await page.addInitScript(value => localStorage.setItem("swb-theme", value), preference)
      await withBootstrapOnly(page)
      const response = await page.goto("/", { waitUntil: "domcontentloaded" })
      expect(response?.ok()).toBe(true)
      const csp = response?.headers()["content-security-policy"]?.split(";").map(directive => directive.trim())
      expect(csp?.filter(directive => directive.startsWith("script-src "))).toEqual(["script-src 'self'"])
      const script = page.locator('head script[src="/app-assets/assets/theme.js"]')
      await expect(script).toHaveCount(1)
      await expect(script).toHaveAttribute("blocking", "render")
      expect(await script.evaluate(node => {
        const script = node as HTMLScriptElement
        return { async: script.async, defer: script.defer, type: script.type, sameOrigin: new URL(script.src).origin === location.origin }
      })).toEqual({ async: false, defer: false, type: "", sameOrigin: true })
      expect(await page.evaluate(readTheme)).toEqual(expectedTheme(preference, os))
      await captureDOMContentLoaded(page, testInfo)
    })
  }
}

test("boot paints a dark preference before the stylesheet arrives", async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: "light" })
  await page.addInitScript(() => localStorage.setItem("swb-theme", "dark"))
  await withBootstrapOnly(page)
  await page.route(/\/app-assets\/assets\/.*\.css$/, route => route.abort())
  await page.goto("/", { waitUntil: "domcontentloaded" })
  await expect(page.locator("html")).toHaveCSS("background-color", colors.dark.rgb)
  await expect(page.locator("html")).toHaveCSS("color-scheme", "dark")
  await captureDOMContentLoaded(page, testInfo)
})

test("Settings updates theme metadata and live System colors", async ({ page }) => {
  const verify = async (preference: Preference, os: Scheme) => {
    await expect.poll(() => page.evaluate(readTheme)).toEqual(expectedTheme(preference, os))
    await expect(page.getByRole("radio", { name: preference, exact: false })).toBeChecked()
  }
  const choose = async (preference: Preference) => {
    await page.getByRole("radio", { name: preference, exact: false }).click()
    expect(await page.evaluate(() => localStorage.getItem("swb-theme"))).toBe(preference)
  }

  await page.emulateMedia({ colorScheme: "light" })
  await page.goto("/settings")
  await verify("system", "light")
  await choose("dark")
  await verify("dark", "light")
  await page.reload()
  await verify("dark", "light")
  await page.emulateMedia({ colorScheme: "dark" })
  await choose("light")
  await verify("light", "dark")
  await choose("system")
  await verify("system", "dark")
  await page.emulateMedia({ colorScheme: "light" })
  await verify("system", "light")
  await page.emulateMedia({ colorScheme: "dark" })
  await verify("system", "dark")
})
