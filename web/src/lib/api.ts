import { queryOptions, type QueryClient } from "@tanstack/react-query"

type Session = {
  signed_in: boolean
  via: "passkey" | "link" | "oidc" | "access" | null
  csrf: string | null
  methods: { passkey: boolean; oidc: { name: string } | null; access: boolean }
  first_run: boolean
}
type Health = { status: "ok" | "degraded"; problems: string[]; commit?: string | null }

export class SignedOutError extends Error {
  constructor() {
    super("Signed out")
    this.name = "SignedOutError"
  }
}

export const authEvents = new EventTarget()

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isSession(value: unknown): value is Session {
  return isObject(value)
    && typeof value.signed_in === "boolean"
    && (value.via === null || value.via === "passkey" || value.via === "link" || value.via === "oidc" || value.via === "access")
    && (value.csrf === null || typeof value.csrf === "string")
    && typeof value.first_run === "boolean"
    && isObject(value.methods)
    && typeof value.methods.passkey === "boolean"
    && typeof value.methods.access === "boolean"
    && (value.methods.oidc === null || (isObject(value.methods.oidc) && typeof value.methods.oidc.name === "string"))
}

function isHealth(value: unknown): value is Health {
  return isObject(value)
    && (value.status === "ok" || value.status === "degraded")
    && Array.isArray(value.problems)
    && value.problems.every((problem) => typeof problem === "string")
    && (value.commit == null || typeof value.commit === "string")
}

type RequestOptions = Omit<RequestInit, "credentials" | "redirect" | "cache"> & { csrf?: string | null }

export async function requestJson<T>(
  path: string,
  validate: (value: unknown) => value is T,
  { csrf, method = "GET", headers: providedHeaders, ...options }: RequestOptions = {},
): Promise<T> {
  const url = new URL(path, location.origin)
  if (url.origin !== location.origin) throw new Error("API requests must be same-origin")

  const headers = new Headers(providedHeaders)
  headers.set("Accept", "application/json")
  headers.delete("X-CSRF-Token")
  method = method.toUpperCase()
  if (method !== "GET") {
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
  if (response.status === 401) {
    const body: unknown = await response.json().catch(() => null)
    if (isObject(body) && body.error === "signed_out") {
      authEvents.dispatchEvent(new Event("signed_out"))
      throw new SignedOutError()
    }
  }
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  const value: unknown = response.status === 204 ? undefined : await response.json()
  if (!validate(value)) throw new Error("Unexpected server response")
  return value
}

export const sessionQuery = queryOptions({
  queryKey: ["session"],
  queryFn: ({ signal }) => requestJson("/auth/session", isSession, { signal }),
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
