import { queryOptions } from "@tanstack/react-query"

type Session = { email: string; csrf: string }
type Health = { status: "ok" | "degraded"; problems: string[]; commit?: string | null }

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isSession(value: unknown): value is Session {
  return isObject(value) && typeof value.email === "string" && !!value.email.trim() && typeof value.csrf === "string" && !!value.csrf
}

function isHealth(value: unknown): value is Health {
  return isObject(value)
    && (value.status === "ok" || value.status === "degraded")
    && Array.isArray(value.problems)
    && value.problems.every((problem) => typeof problem === "string")
    && (value.commit == null || typeof value.commit === "string")
}

async function getJson<T>(url: string, signal: AbortSignal, validate: (value: unknown) => value is T): Promise<T> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    redirect: "error",
    cache: "no-store",
    signal,
  })
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  const value: unknown = await response.json()
  if (!validate(value)) throw new Error("Unexpected server response")
  return value
}

export const sessionQuery = queryOptions({
  queryKey: ["session"],
  queryFn: ({ signal }) => getJson("/api/session", signal, isSession),
  retry: false,
  staleTime: 60_000,
})

export const healthQuery = queryOptions({
  queryKey: ["health"],
  queryFn: ({ signal }) => getJson("/api/health", signal, isHealth),
  staleTime: 15_000,
  refetchInterval: 30_000,
})
