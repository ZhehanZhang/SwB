import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { runInNewContext } from "node:vm"

const script = readFileSync(new URL("../public/assets/theme.js", import.meta.url), "utf8")

function boot({ prefersDark = false, values = new Map(), blocked = false } = {}) {
  const media = Object.assign(new EventTarget(), { matches: prefersDark })
  const rootClasses = new Set()
  const window = Object.assign(new EventTarget(), {
    matchMedia(query) {
      assert.equal(query, "(prefers-color-scheme: dark)")
      return media
    },
  })
  const document = { documentElement: { style: {}, classList: {
    toggle(name, enabled) { if (enabled) rootClasses.add(name); else rootClasses.delete(name) },
  } } }
  const localStorage = {
    getItem(key) { if (blocked) throw Error("Storage disabled"); return values.get(key) ?? null },
    setItem(key, value) { if (blocked) throw Error("Storage disabled"); values.set(key, value) },
  }
  runInNewContext(script, { window, document, localStorage, Event })
  return {
    theme: window.swbTheme,
    dark: () => rootClasses.has("dark"),
    scheme: () => document.documentElement.style.colorScheme,
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
  app.systemDark(false)
  assert.equal(app.dark(), false)
  assert.equal(app.scheme(), "light")
  app.systemDark(true)
  assert.equal(app.dark(), true)
})

test("explicit light and dark override the OS and survive reloads", () => {
  const values = new Map()
  const app = boot({ values, prefersDark: true })
  app.theme.set("light")
  assert.equal(app.dark(), false)
  app.systemDark(false)
  app.systemDark(true)
  assert.equal(app.dark(), false)
  assert.equal(boot({ values, prefersDark: true }).theme.get(), "light")
  app.theme.set("dark")
  assert.equal(boot({ values }).dark(), true)
  app.theme.set("system")
  assert.equal(boot({ values }).dark(), false)
})

test("ignores invalid preferences and keeps working without storage", () => {
  const invalid = boot({ values: new Map([["swb-theme", "other"]]), prefersDark: true })
  assert.equal(invalid.theme.get(), "system")
  assert.equal(invalid.dark(), true)
  const blocked = boot({ blocked: true })
  blocked.theme.set("dark")
  assert.equal(blocked.dark(), true)
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
