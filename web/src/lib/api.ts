import { queryOptions, type QueryClient } from "@tanstack/react-query"

export type Session = {
  signed_in: boolean
  via: "passkey" | "link" | "oidc" | "access" | "setup" | null
  csrf: string | null
  methods: { passkeys: number; oidc: { name: string } | null; access: boolean; setup?: boolean }
}
type Health = { status: "ok" | "degraded"; problems: string[]; commit?: string | null }

export class SignedOutError extends Error {
  constructor() {
    super("Signed out")
    this.name = "SignedOutError"
  }
}

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code?: string) {
    super(`Request failed (${status})`)
    this.name = "ApiError"
  }
}

export const authEvents = new EventTarget()

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function sessionShape(value: unknown): value is Session {
  if (!isObject(value)) return false
  const { signed_in, via, csrf, methods } = value
  return typeof signed_in === "boolean"
    && (via === null || via === "passkey" || via === "link" || via === "oidc" || via === "access" || via === "setup")
    && (csrf === null || typeof csrf === "string")
    && isObject(methods)
    && typeof methods.passkeys === "number" && Number.isSafeInteger(methods.passkeys) && methods.passkeys >= 0
    && typeof methods.access === "boolean"
    && (methods.setup === undefined || typeof methods.setup === "boolean")
    && (methods.oidc === null || (isObject(methods.oidc) && typeof methods.oidc.name === "string"))
}

function isHealth(value: unknown): value is Health {
  return isObject(value)
    && (value.status === "ok" || value.status === "degraded")
    && Array.isArray(value.problems)
    && value.problems.every((problem) => typeof problem === "string")
    && (value.commit == null || typeof value.commit === "string")
}

type RequestOptions = Omit<RequestInit, "credentials" | "redirect" | "cache"> & { csrf?: string | null }
const originOnlyPosts = new Set(["/auth/link", "/auth/setup", "/auth/passkey/login/options", "/auth/passkey/login/verify"])

async function apiRequest(
  path: string,
  accept: string,
  { csrf, method = "GET", headers: providedHeaders, ...options }: RequestOptions = {},
) {
  const url = new URL(path, location.origin)
  if (url.origin !== location.origin) throw new Error("API requests must be same-origin")

  const headers = new Headers(providedHeaders)
  headers.set("Accept", accept)
  headers.delete("X-CSRF-Token")
  method = method.toUpperCase()
  if (method !== "GET" && !(method === "POST" && originOnlyPosts.has(url.pathname))) {
    if (!csrf) throw new Error("A CSRF token is required")
    headers.set("X-CSRF-Token", csrf)
  }

  const response = await fetch(url, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    redirect: "error",
    cache: "no-store",
  })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    const code = isObject(body) && typeof body.error === "string" ? body.error : undefined
    if (response.status === 401 && code === "signed_out") {
      authEvents.dispatchEvent(new Event("signed_out"))
      throw new SignedOutError()
    }
    throw new ApiError(response.status, code)
  }
  return response
}

export async function requestJson<T>(
  path: string,
  validate: (value: unknown) => value is T,
  options?: RequestOptions,
): Promise<T> {
  const response = await apiRequest(path, "application/json", options)
  const value: unknown = response.status === 204 ? undefined : await response.json()
  if (!validate(value)) throw new Error("Unexpected server response")
  return value
}

export async function requestMarkdown(path: string, signal?: AbortSignal): Promise<string> {
  return (await apiRequest(path, "text/markdown", { signal })).text()
}

export async function requestZip(path: string) {
  const response = await apiRequest(path, "application/zip")
  if (response.headers.get("Content-Type") !== "application/zip") throw new Error("Unexpected server response")
  return response
}

export const sessionQuery = queryOptions({
  queryKey: ["session"],
  queryFn: ({ signal }) => requestJson("/auth/session", sessionShape, { signal }),
  retry: false,
  staleTime: 60_000,
})

export function markSignedOut(client: QueryClient) {
  const session = client.getQueryData(sessionQuery.queryKey)
  if (!session) return
  const filter = { queryKey: sessionQuery.queryKey, exact: true }
  void client.cancelQueries(filter)
  client.setQueryData(sessionQuery.queryKey, { ...session, signed_in: false, csrf: null, via: null })
  void client.invalidateQueries({ ...filter, refetchType: "none" })
}

export const healthQuery = queryOptions({
  queryKey: ["health"],
  queryFn: ({ signal }) => requestJson("/api/health", isHealth, { signal }),
  staleTime: 15_000,
  refetchInterval: 30_000,
})
