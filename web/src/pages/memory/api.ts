import { requestJson } from "@/lib/api"

type JsonObject = Record<string, unknown>

export type MemoryNote = {
  id: string
  scope: string
  namespace: string
  key: string | null
  title: string
  summary: string
  writer: string
  updated_at: string | null
}

type Revision = { at: string | null; source: string; action: string }

export type MemoryDetail = MemoryNote & {
  body: string
  tags: string[]
  history: Revision[]
}

type MemoryList = {
  scopes: Record<string, Record<string, number>>
  items: MemoryNote[]
  next_offset?: number | null
}

type MemoryProject = { scope: string; archived: boolean }

export type MemoryAction =
  | { kind: "move"; toScope: string }
  | { kind: "promote" }
  | { kind: "delete" }

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isDate(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && Number.isFinite(Date.parse(value)))
}

function isNote(value: unknown): value is MemoryNote {
  return isObject(value)
    && typeof value.id === "string"
    && typeof value.scope === "string"
    && typeof value.namespace === "string"
    && (value.key === null || typeof value.key === "string")
    && typeof value.title === "string"
    && typeof value.summary === "string"
    && typeof value.writer === "string"
    && isDate(value.updated_at)
}

function isList(value: unknown): value is MemoryList {
  return isObject(value)
    && isObject(value.scopes)
    && Object.values(value.scopes).every((counts) => isObject(counts)
      && Object.values(counts).every((count) => typeof count === "number" && Number.isSafeInteger(count) && count >= 0))
    && Array.isArray(value.items)
    && value.items.every(isNote)
    && (value.next_offset == null || (typeof value.next_offset === "number" && Number.isSafeInteger(value.next_offset) && value.next_offset > 0))
}

function isDetail(value: unknown): value is { entry: MemoryDetail } {
  if (!isObject(value)) return false
  const entry = value.entry
  return isObject(entry)
    && typeof entry.body === "string"
    && Array.isArray(entry.tags)
    && entry.tags.every((tag) => typeof tag === "string")
    && Array.isArray(entry.history)
    && entry.history.every((revision) => isObject(revision) && isDate(revision.at)
      && typeof revision.source === "string" && typeof revision.action === "string")
    && isNote(entry)
}

function isProjects(value: unknown): value is { projects: MemoryProject[] } {
  return isObject(value) && Array.isArray(value.projects)
    && value.projects.every((project) => isObject(project) && typeof project.scope === "string" && typeof project.archived === "boolean")
}

export function fetchMemoryList(params: Record<string, string>, signal: AbortSignal) {
  return requestJson(`/api/memory?${new URLSearchParams(params)}`, isList, { signal })
}

export async function fetchMemoryDetail(id: string, signal: AbortSignal) {
  const { entry } = await requestJson(`/api/memory?${new URLSearchParams({ id })}`, isDetail, { signal })
  return entry
}

export async function fetchMoveScopes(signal: AbortSignal) {
  const { projects } = await requestJson("/api/projects", isProjects, { signal })
  return projects.filter((project) => !project.archived).map((project) => project.scope)
}

export async function applyMemoryAction(id: string, action: MemoryAction, csrf: string) {
  const path = `/api/memory/${encodeURIComponent(id)}`
  if (action.kind === "delete") {
    await requestJson(path, (value): value is { deleted: string } => isObject(value) && value.deleted === id, {
      method: "DELETE",
      csrf,
    })
    return
  }
  const to = action.kind === "promote" ? "general" : action.toScope
  await requestJson(`${path}/${action.kind}`, (value): value is { moved: string; to: string } =>
    isObject(value) && value.moved === id && value.to === to, {
    method: "POST",
    csrf,
    ...(action.kind === "move" && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to_scope: to }),
    }),
  })
}
