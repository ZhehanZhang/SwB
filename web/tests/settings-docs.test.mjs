import assert from "node:assert/strict"
import test from "node:test"
import { consoleDocs, selectDocsLocale } from "../src/lib/console-docs.ts"

const base = "https://swb.one/docs"
const publicUrl = "https://swb.example.test:9443"
const prefill = "swb=https%3A%2F%2Fswb.example.test%3A9443"

for (const [platform, path] of [
  ["fly", "deploy#fly"],
  ["render", "deploy#render"],
  ["railway", "deploy#railway"],
  ["compose", "deploy#docker-compose"],
  ["local", "install#install-and-start"],
]) {
  test(`links to the ${platform} deployment guide`, () => {
    const { deploy, operate, tunnel } = consoleDocs({ platform, publicUrl, languages: ["en-US"] })
    assert.equal(deploy, `${base}/${path}&${prefill}`)
    assert.equal(operate, `${base}/operate#backups-and-upgrades&${prefill}`)
    assert.equal(tunnel, null)
  })
}

test("falls back to the deployment guide for unknown and unset platforms", () => {
  assert.equal(consoleDocs({ platform: "constructor", publicUrl, languages: [] }).deploy, `${base}/deploy#${prefill}`)
  assert.equal(consoleDocs({ languages: [] }).deploy, `${base}/deploy`)
  assert.equal(consoleDocs({ platform: "fly", languages: [] }).deploy, `${base}/deploy#fly`)
})

test("uses the selected docs locale and links to detected Cloudflare Tunnel", () => {
  const docs = consoleDocs({ platform: "local", publicUrl: "https://example.trycloudflare.com", languages: ["zh-TW"] })
  assert.equal(docs.deploy, "https://swb.one/zh-Hant/docs/install#install-and-start&swb=https%3A%2F%2Fexample.trycloudflare.com")
  assert.equal(docs.tunnel, "https://swb.one/zh-Hant/docs/deploy#cloudflare-tunnel&swb=https%3A%2F%2Fexample.trycloudflare.com")
  assert.equal(consoleDocs({ platform: "local", publicUrl: "https://example.trycloudflare.com.evil.test", languages: ["nl"] }).tunnel, null)
})

test("prefers the console locale, then the first supported browser language", () => {
  assert.equal(selectDocsLocale("de-DE", ["fr-FR", "en"]), "de")
  assert.equal(selectDocsLocale("nl-NL", ["pt-BR", "fr-CA", "es"]), "fr")
  assert.equal(selectDocsLocale(undefined, ["en-GB", "ja"]), "en")
  assert.equal(selectDocsLocale(undefined, ["nl"]), "en")
  assert.equal(selectDocsLocale(undefined, []), "en")
})

test("chooses the Chinese documentation script", () => {
  for (const language of ["zh", "zh-CN", "zh-SG", "zh-Hans", "zh-GH"]) {
    assert.equal(selectDocsLocale(undefined, [language]), "zh-Hans")
  }
  for (const language of ["zh-TW", "zh-HK", "zh-MO", "zh-Hant-TW"]) {
    assert.equal(selectDocsLocale(undefined, [language]), "zh-Hant")
  }
})

test("links to the exact Memory backup section in the selected locale", () => {
  assert.equal(consoleDocs({ publicUrl, languages: ["en"] }).memoryBackup, `${base}/memory#back-up-to-git&${prefill}`)
  assert.equal(consoleDocs({ publicUrl, languages: ["nl", "fr"] }).memoryBackup,
    `https://swb.one/fr/docs/memory#sauvegarder-dans-git&${prefill}`)
  assert.equal(consoleDocs({ languages: ["zh-HK"] }).memoryBackup,
    "https://swb.one/zh-Hant/docs/memory#%E5%82%99%E4%BB%BD%E5%88%B0-git")
})
