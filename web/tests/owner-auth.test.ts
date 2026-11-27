import assert from "node:assert/strict"
import { test, type TestContext } from "node:test"
import { QueryClient } from "@tanstack/react-query"
import { ApiError, authEvents, requestJson, sessionQuery, SignedOutError } from "../src/lib/api.js"
import { addPasskey, exchangeLinkFromLocation, listPasskeys, oidcStart, removePasskey, safeNext, signInWithPasskey } from "../src/lib/owner-auth.js"

const origin = "https://switchboard.example.test"
type RequestCall = { path: string; method?: string; csrf: string | null; body?: unknown }

function mockBrowser(t: TestContext, path = "/login") {
  const url = new URL(path, origin)
  const state = { key: "router-key", idx: 1 }
  const replacements: string[] = []
  for (const [key, value] of Object.entries({
    location: url,
    history: {
      state,
      replaceState(saved: unknown, _title: string, next: string) {
        assert.equal(saved, state)
        replacements.push(next)
        url.href = new URL(next, origin).href
      },
    },
  })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { configurable: true, value })
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, key, previous)
      else Reflect.deleteProperty(globalThis, key)
    })
  }
  return { url, replacements }
}

function mockRequests(t: TestContext, replies: Array<() => Response>) {
  const calls: RequestCall[] = []
  t.mock.method(globalThis, "fetch", async (url: URL, options: RequestInit) => {
    assert.equal(url.origin, origin)
    assert.equal(options.credentials, "same-origin")
    assert.equal(options.redirect, "error")
    assert.equal(options.cache, "no-store")
    const headers = new Headers(options.headers)
    assert.equal(headers.get("Accept"), "application/json")
    if (options.body) assert.equal(headers.get("Content-Type"), "application/json")
    calls.push({
      path: url.pathname, method: options.method, csrf: headers.get("X-CSRF-Token"),
      body: options.body ? JSON.parse(String(options.body)) : undefined,
    })
    const reply = replies.shift()
    assert.ok(reply, "Unexpected fetch")
    return reply()
  })
  return calls
}

const empty = () => new Response(null, { status: 204 })

test("only safe same-origin paths are preserved, including for OIDC", (t) => {
  mockBrowser(t)
  assert.equal(safeNext("/projects/work?view=routes#details"), "/projects/work?view=routes#details")
  for (const path of [null, "https://switchboard.example.test/settings", "//other.test", "/\\other.test", "/..//other.test", "/\t/other.test", "/%2Fother.test", "/login/", "/%6cogin"]) {
    assert.equal(safeNext(path), "/")
  }
  assert.equal(oidcStart("/settings?tab=auth#sign-in"), "/auth/oidc/start?next=%2Fsettings%3Ftab%3Dauth%23sign-in")
})

test("clears the link fragment before fetch and does not post twice", async (t) => {
  const { url, replacements } = mockBrowser(t, "/login?next=/settings#t=test-once")
  const calls = mockRequests(t, [() => {
    assert.equal(url.hash, "")
    return empty()
  }])
  const first = exchangeLinkFromLocation()
  assert.equal(exchangeLinkFromLocation(), undefined)
  await first
  assert.deepEqual(replacements, ["/login?next=/settings"])
  assert.deepEqual(calls, [{ path: "/auth/link", method: "POST", csrf: null, body: { token: "test-once" } }])
})

test("keeps expired-link errors distinct from expired sessions", async (t) => {
  mockBrowser(t, "/login#t=expired-fixture")
  mockRequests(t, [
    () => Response.json({ error: "link_expired" }, { status: 401 }),
    () => Response.json({ error: "signed_out" }, { status: 401 }),
  ])
  let signOuts = 0
  function signedOut() { signOuts += 1 }
  authEvents.addEventListener("signed_out", signedOut)
  t.after(() => authEvents.removeEventListener("signed_out", signedOut))
  await assert.rejects(exchangeLinkFromLocation()!, (error) => error instanceof ApiError && error.code === "link_expired")
  assert.equal(signOuts, 0)
  await assert.rejects(listPasskeys(), SignedOutError)
  assert.equal(signOuts, 1)
})

test("requires a non-negative passkey count in the session contract", async (t) => {
  mockBrowser(t)
  const access = { signed_in: true, via: "access", csrf: "csrf-test" }
  mockRequests(t, [
    () => Response.json({ ...access, methods: { passkeys: 2, oidc: { name: "My ID" }, access: true } }),
    () => Response.json({ ...access, methods: { passkey: false, oidc: null, access: true }, first_run: true }),
    () => Response.json({ ...access, methods: { passkeys: -1, passkey: true, oidc: null, access: true } }),
  ])
  assert.deepEqual(await new QueryClient().fetchQuery(sessionQuery), { ...access, methods: { passkeys: 2, oidc: { name: "My ID" }, access: true } })
  await assert.rejects(new QueryClient().fetchQuery(sessionQuery), /Unexpected server response/)
  await assert.rejects(new QueryClient().fetchQuery(sessionQuery), /Unexpected server response/)
})

test("uses native WebAuthn JSON conversion for get and create, with the right CSRF", async (t) => {
  mockBrowser(t)
  const decoded = { challenge: new Uint8Array([1]) }
  const assertion = { id: "test-key", rawId: "AQ", type: "public-key", response: { signature: "AQ" } }
  const creation = { id: "new-key", rawId: "Ag", type: "public-key", response: { attestationObject: "Ag" } }
  const user = { id: "Ag", name: "owner", displayName: "Owner" }
  class MockCredential {
    constructor(private readonly value: object) {}
    toJSON() { return this.value }
    static parseRequestOptionsFromJSON(publicKey: object) {
      assert.deepEqual(publicKey, { challenge: "AQ" })
      return decoded
    }
    static parseCreationOptionsFromJSON(publicKey: object) {
      assert.deepEqual(publicKey, { challenge: "AQ", user })
      return { ...decoded, user: { ...user, id: new Uint8Array([2]) } }
    }
  }
  const existingNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator")
  Object.defineProperty(globalThis, "PublicKeyCredential", { configurable: true, value: MockCredential })
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { credentials: {
    async get(options: object) {
      assert.deepEqual(options, { publicKey: decoded })
      return new MockCredential(assertion)
    },
    async create(options: object) {
      assert.deepEqual(options, { publicKey: { ...decoded, user: { ...user, id: new Uint8Array([2]) } } })
      return new MockCredential(creation)
    },
  } } })
  t.after(() => {
    Reflect.deleteProperty(globalThis, "PublicKeyCredential")
    if (existingNavigator) Object.defineProperty(globalThis, "navigator", existingNavigator)
    else Reflect.deleteProperty(globalThis, "navigator")
  })

  const calls = mockRequests(t, [
    () => Response.json({ publicKey: { challenge: "AQ" } }),
    empty,
    () => Response.json({ publicKey: { challenge: "AQ", user } }),
    () => Response.json({ id: "new-key" }, { status: 201 }),
  ])
  await signInWithPasskey()
  assert.deepEqual(await addPasskey("csrf-test", "Laptop"), { id: "new-key" })
  assert.deepEqual(calls, [
    { path: "/auth/passkey/login/options", method: "POST", csrf: null, body: undefined },
    { path: "/auth/passkey/login/verify", method: "POST", csrf: null, body: { credential: assertion } },
    { path: "/auth/passkey/register/options", method: "POST", csrf: "csrf-test", body: undefined },
    { path: "/auth/passkey/register/verify", method: "POST", csrf: "csrf-test", body: { credential: creation, name: "Laptop" } },
  ])
})

test("lists and deletes passkeys and distinguishes removal refusal from missing APIs", async (t) => {
  mockBrowser(t)
  const keys = [{ id: "test-key", name: "Laptop", created_at: "2026-11-26T00:00:00Z", last_used_at: null }]
  const calls = mockRequests(t, [
    () => Response.json(keys),
    () => Response.json({ error: "last_method" }, { status: 409 }),
    empty,
    () => new Response("Not found", { status: 404 }),
  ])
  assert.deepEqual(await listPasskeys(), keys)
  await assert.rejects(removePasskey("csrf-test", "test-key"), (error) => error instanceof ApiError && error.code === "last_method")
  await removePasskey("csrf-test", "test-key")
  assert.equal(await listPasskeys(), null)
  await assert.rejects(removePasskey(null, "test-key"), /CSRF/)
  await assert.rejects(requestJson("/auth/logout", (_): _ is undefined => true, { method: "POST" }), /CSRF/)
  assert.deepEqual(calls.map(({ path, method, csrf }) => [path, method, csrf]), [
    ["/auth/passkeys", "GET", null],
    ["/auth/passkeys/test-key", "DELETE", "csrf-test"],
    ["/auth/passkeys/test-key", "DELETE", "csrf-test"],
    ["/auth/passkeys", "GET", null],
  ])
})
