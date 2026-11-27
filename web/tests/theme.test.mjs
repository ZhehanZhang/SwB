import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { runInNewContext } from "node:vm"

const script = readFileSync(new URL("../../src/switchboard/static/theme.js", import.meta.url), "utf8")

function boot({ prefersDark = false, values = new Map(), blocked = false, themeColors = true, cookie = "" } = {}) {
  const media = Object.assign(new EventTarget(), { matches: prefersDark })
  const rootClasses = new Set()
  const window = Object.assign(new EventTarget(), {
    matchMedia(query) {
      assert.equal(query, "(prefers-color-scheme: dark)")
      return media
    },
  })
  const schemeMeta = { content: "light dark" }
  const lightMeta = { content: "#ffffff" }
  const darkMeta = { content: "#09090b" }
  const metas = new Map([
    ['meta[name="color-scheme"]', schemeMeta],
    ['meta[name="theme-color"][media="(prefers-color-scheme: light)"]', themeColors ? lightMeta : null],
    ['meta[name="theme-color"][media="(prefers-color-scheme: dark)"]', themeColors ? darkMeta : null],
  ])
  const document = {
    cookie,
    documentElement: { style: {}, dataset: {}, classList: {
      toggle(name, enabled) { if (enabled) rootClasses.add(name); else rootClasses.delete(name) },
    } },
    querySelector(selector) { return metas.get(selector) ?? null },
  }
  const localStorage = {
    getItem(key) { if (blocked) throw Error("Storage disabled"); return values.get(key) ?? null },
    setItem(key, value) { if (blocked) throw Error("Storage disabled"); values.set(key, value) },
  }
  runInNewContext(script, { window, document, localStorage, Event })
  return {
    theme: window.swbTheme,
    dark: () => rootClasses.has("dark"),
    cookie: () => document.cookie,
    scheme: () => document.documentElement.style.colorScheme,
    background: () => document.documentElement.style.backgroundColor,
    metas: () => [schemeMeta.content, lightMeta.content, darkMeta.content],
    systemDark(matches) {
      media.matches = matches
      media.dispatchEvent(new Event("change"))
    },
    storageChanged(key) {
      window.dispatchEvent(Object.assign(new Event("storage"), { key }))
    },
  }
}

test("applies system preference on boot and follows OS changes", () => {
  const app = boot({ prefersDark: true })
  assert.equal(app.theme.get(), "system")
  assert.equal(app.dark(), true)
  assert.equal(app.scheme(), "dark")
  assert.equal(app.background(), "#09090b")
  assert.deepEqual(app.metas(), ["light dark", "#ffffff", "#09090b"])
  app.systemDark(false)
  assert.equal(app.dark(), false)
  assert.equal(app.scheme(), "light")
  assert.equal(app.background(), "#ffffff")
  assert.deepEqual(app.metas(), ["light dark", "#ffffff", "#09090b"])
  app.systemDark(true)
  assert.equal(app.dark(), true)
})

test("reads prior theme preferences and leaves legacy cookies unchanged", () => {
  const cookie = "siteAppearance=light; swbAppearance=dark"
  const values = new Map()
  const app = boot({ cookie, values })
  assert.equal(app.theme.get(), "dark")
  assert.equal(boot({ cookie: "siteAppearance=light" }).theme.get(), "light")
  assert.equal(boot({ values: new Map([["theme", "dark"]]) }).theme.get(), "dark")
  app.theme.set("system")
  assert.equal(app.cookie(), cookie)
  assert.equal(boot({ cookie, values }).theme.get(), "system")
})

test("explicit light and dark override the OS and survive reloads", () => {
  const values = new Map()
  const app = boot({ values, prefersDark: true })
  app.theme.set("light")
  assert.equal(app.dark(), false)
  assert.deepEqual(app.metas(), ["light", "#ffffff", "#ffffff"])
  app.systemDark(false)
  app.systemDark(true)
  assert.equal(app.dark(), false)
  assert.equal(boot({ values, prefersDark: true }).theme.get(), "light")
  app.theme.set("dark")
  assert.equal(boot({ values }).dark(), true)
  assert.deepEqual(app.metas(), ["dark", "#09090b", "#09090b"])
  app.theme.set("system")
  assert.equal(boot({ values }).dark(), false)
  assert.deepEqual(app.metas(), ["light dark", "#ffffff", "#09090b"])
})

test("ignores invalid preferences and keeps working without storage", () => {
  const invalid = boot({ values: new Map([["swb-theme", "other"]]), prefersDark: true })
  assert.equal(invalid.theme.get(), "system")
  assert.equal(invalid.dark(), true)
  const blocked = boot({ blocked: true })
  blocked.theme.set("dark")
  assert.equal(blocked.dark(), true)
})

test("color-scheme still updates on pages without theme-color tags", () => {
  const app = boot({ themeColors: false })
  app.theme.set("dark")
  assert.equal(app.scheme(), "dark")
  assert.equal(app.metas()[0], "dark")
  assert.equal(app.background(), undefined)
})

test("syncs preference changes from another tab", () => {
  const values = new Map()
  const app = boot({ values, prefersDark: true })
  values.set("swb-theme", "light")
  app.storageChanged("swb-theme")
  assert.equal(app.theme.get(), "light")
  assert.equal(app.dark(), false)
  values.clear()
  app.storageChanged(null)
  assert.equal(app.theme.get(), "system")
  assert.equal(app.dark(), true)
})
