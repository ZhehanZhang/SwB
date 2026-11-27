import { ApiError, isObject, requestJson } from "./api.js"

export type Passkey = {
  id: string
  name: string
  created_at: string
  last_used_at: string | null
}

const noContent = (value: unknown): value is undefined => value === undefined
const json = (body: object) => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

export function safeNext(next: string | null, origin = location.origin): string {
  if (!next?.startsWith("/") || next.startsWith("//") || /[\\\u0000-\u0020\u007f]/.test(next)) return "/"
  try {
    const url = new URL(next, origin)
    const path = decodeURIComponent(url.pathname)
    if (url.origin === origin && !path.startsWith("//") && !path.includes("\\") && !/^\/login\/*$/i.test(path)) {
      return url.pathname + url.search + url.hash
    }
  } catch {
    return "/"
  }
  return "/"
}

export function oidcStart(next: string): string {
  return `/auth/oidc/start?${new URLSearchParams({ next: safeNext(next) })}`
}

export function exchangeLinkFromLocation(): Promise<void> | undefined {
  const token = new URLSearchParams(location.hash.slice(1)).get("t")
  if (token === null) return
  history.replaceState(history.state, "", location.pathname + location.search)
  if (!token) return Promise.reject(new ApiError(401, "link_expired"))
  return requestJson("/auth/link", noContent, { method: "POST", ...json({ token }) })
}

export function supportsPasskeys() {
  return typeof PublicKeyCredential !== "undefined"
    && typeof PublicKeyCredential.parseRequestOptionsFromJSON === "function"
    && typeof PublicKeyCredential.parseCreationOptionsFromJSON === "function"
    && typeof PublicKeyCredential.prototype.toJSON === "function"
    && Boolean(navigator.credentials)
}

function isRequestOptions(value: unknown): value is { publicKey: PublicKeyCredentialRequestOptionsJSON } {
  return isObject(value) && isObject(value.publicKey) && typeof value.publicKey.challenge === "string"
}

function isCreationOptions(value: unknown): value is { publicKey: PublicKeyCredentialCreationOptionsJSON } {
  return isRequestOptions(value) && isObject(value.publicKey)
    && isObject(value.publicKey.user) && typeof value.publicKey.user.id === "string"
}

function serializeCredential(credential: Credential | null) {
  if (!(credential instanceof PublicKeyCredential)) throw new Error("No passkey returned")
  return credential.toJSON()
}

export async function signInWithPasskey() {
  if (!supportsPasskeys()) throw new Error("Passkeys are unavailable")
  const { publicKey } = await requestJson("/auth/passkey/login/options", isRequestOptions, { method: "POST" })
  const credential = await navigator.credentials.get({ publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(publicKey) })
  await requestJson("/auth/passkey/login/verify", noContent, {
    method: "POST", ...json({ credential: serializeCredential(credential) }),
  })
}

export async function addPasskey(csrf: string | null, name: string) {
  if (!supportsPasskeys()) throw new Error("Passkeys are unavailable")
  const { publicKey } = await requestJson("/auth/passkey/register/options", isCreationOptions, { method: "POST", csrf })
  const credential = await navigator.credentials.create({ publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(publicKey) })
  return requestJson("/auth/passkey/register/verify", (value): value is { id: string } => isObject(value) && typeof value.id === "string", {
    method: "POST", csrf, ...json({ credential: serializeCredential(credential), name }),
  })
}

export async function listPasskeys(signal?: AbortSignal): Promise<Passkey[] | null> {
  try {
    return await requestJson("/auth/passkeys", (value): value is Passkey[] => Array.isArray(value) && value.every((item) =>
      isObject(item) && typeof item.id === "string" && typeof item.name === "string" && typeof item.created_at === "string"
      && (item.last_used_at === null || typeof item.last_used_at === "string"),
    ), { signal })
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 405)) return null
    throw error
  }
}

export function removePasskey(csrf: string | null, id: string) {
  if (!id || id === "." || id === "..") throw new Error("Invalid passkey ID")
  return requestJson(`/auth/passkeys/${encodeURIComponent(id)}`, noContent, { method: "DELETE", csrf })
}
