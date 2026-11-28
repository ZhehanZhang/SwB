import { isObject, requestJson, requestZip } from "@/lib/api"

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

export type Revision = { rev: number; at: string | null; source: string; action: string }

export type MemoryDetail = MemoryNote & {
  path: string
  created_by: string
  content_by: string
  sync_source: string | null
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
  | { kind: "edit"; title: string; body: string; tags: string[] }
  | { kind: "rename"; path: string }
  | { kind: "restore"; rev: number }

export type BackupConfig = {
  remote: string
  branch: string
  author_name: string
  author_email: string
  enabled: boolean
}

export type BackupStatus = BackupConfig & {
  credential_saved: boolean
  last_success_at: string | null
  last_commit: string | null
  last_error: string | null
  git_available: boolean
}

const archiveCounts = ["created", "updated", "unchanged", "conflict", "skipped"] as const
export type ArchiveResult = Record<typeof archiveCounts[number], number>

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
    && typeof entry.path === "string"
    && typeof entry.created_by === "string"
    && typeof entry.content_by === "string"
    && isNullableText(entry.sync_source)
    && typeof entry.body === "string"
    && Array.isArray(entry.tags)
    && entry.tags.every((tag) => typeof tag === "string")
    && Array.isArray(entry.history)
    && entry.history.every((revision) => isObject(revision) && typeof revision.rev === "number"
      && Number.isSafeInteger(revision.rev) && revision.rev > 0 && isDate(revision.at)
      && typeof revision.source === "string" && typeof revision.action === "string")
    && isNote(entry)
}

function isNullableText(value: unknown): value is string | null {
  return value === null || typeof value === "string"
}

function isBackupStatus(value: unknown): value is BackupStatus {
  return isObject(value)
    && typeof value.remote === "string"
    && typeof value.branch === "string"
    && typeof value.author_name === "string"
    && typeof value.author_email === "string"
    && typeof value.enabled === "boolean"
    && typeof value.credential_saved === "boolean"
    && isDate(value.last_success_at)
    && isNullableText(value.last_commit)
    && isNullableText(value.last_error)
    && typeof value.git_available === "boolean"
}

function isArchiveResult(value: unknown): value is ArchiveResult {
  return isObject(value) && archiveCounts.every((key) => typeof value[key] === "number"
    && Number.isSafeInteger(value[key]) && value[key] >= 0)
}

function isMemorySettings(value: unknown): value is { sync: boolean } {
  return isObject(value) && typeof value.sync === "boolean"
}

function jsonPost(csrf: string, params: JsonObject) {
  return { method: "POST", csrf, headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) }
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
  if (action.kind === "edit") {
    const { title, body, tags } = action
    await requestJson(`${path}/edit`, (value): value is { id: string } => isObject(value) && value.id === id,
      jsonPost(csrf, { title, body, tags }))
    return
  }
  if (action.kind === "rename") {
    await requestJson(`${path}/rename`, (value): value is { renamed: string } => isObject(value) && value.renamed === id,
      jsonPost(csrf, { path: action.path }))
    return
  }
  if (action.kind === "restore") {
    await requestJson(`${path}/restore`, (value): value is { id: string; restored: number } =>
      isObject(value) && value.id === id && value.restored === action.rev, jsonPost(csrf, { rev: action.rev }))
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

export function fetchMemorySettings(signal: AbortSignal) {
  return requestJson("/api/memory/settings", isMemorySettings, { signal })
}

export function saveMemorySync(sync: boolean, csrf: string) {
  return requestJson("/api/memory/settings", isMemorySettings, jsonPost(csrf, { sync }))
}

export async function downloadMemory() {
  const response = await requestZip("/api/memory/export")
  const filename = response.headers.get("Content-Disposition")?.match(/filename="(swb-memory-\d{8}\.zip)"/)?.[1]
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement("a")
  link.href = url
  link.download = filename ?? "swb-memory.zip"
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

export function importMemory(file: File, csrf: string) {
  return requestJson("/api/memory/import", isArchiveResult, {
    method: "POST",
    csrf,
    headers: { "Content-Type": "application/zip" },
    body: file,
  })
}

export function fetchBackup(signal: AbortSignal) {
  return requestJson("/api/memory/backup", isBackupStatus, { signal })
}

export function saveBackup(config: BackupConfig, csrf: string, credential?: string) {
  return requestJson("/api/memory/backup", isBackupStatus, jsonPost(csrf, { ...config, credential }))
}

export function backupNow(csrf: string) {
  return requestJson("/api/memory/backup/now", isBackupStatus, { method: "POST", csrf })
}

export function restoreBackup(csrf: string) {
  return requestJson("/api/memory/backup/restore", isArchiveResult, { method: "POST", csrf })
}
