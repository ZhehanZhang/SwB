import { queryOptions } from "@tanstack/react-query"
import { isObject, requestJson } from "@/lib/api"

const modes = ["off", "watch", "assist", "triage", "work"] as const
const actions = ["reply", "label", "comment", "open_issue", "link_duplicate", "notify_owner"] as const
const sources = ["override", "channel", "space", "project", "default", "legacy"] as const
const kinds = ["channel", "thread", "forum", "group", "topic", "repo", "dm"] as const

export type Mode = typeof modes[number]
export type TriageAction = typeof actions[number]
export type ModeRow = {
  block: string
  space: string
  channel: string
  name: string | null
  kind: typeof kinds[number]
  mode: Mode
  source: typeof sources[number]
  project: string | null
  target: string | null
  actions: TriageAction[]
  digest: { pending: number; next_at: number | null } | null
  problems: string[]
}
export type ModesStatus = {
  schema: { modes: Mode[]; dm_modes: Mode[]; actions: TriageAction[]; default: Mode }
  spaces: ModeRow[]
  dm: { block: string; mode: Mode; source: ModeRow["source"] }[]
  github?: { installation: Record<string, string> | null; checked_at: number | null } | null
}

function oneOf<T extends string>(value: unknown, options: readonly T[]): value is T {
  return options.some(option => option === value)
}

function stringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string"
}

function timestamp(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 8.64e12)
}

function isModeRow(row: unknown): row is ModeRow {
  if (!isObject(row)) return false
  const { digest } = row
  return typeof row.block === "string" && typeof row.space === "string" && typeof row.channel === "string"
    && stringOrNull(row.name) && stringOrNull(row.project) && stringOrNull(row.target)
    && oneOf(row.kind, kinds) && oneOf(row.mode, modes) && oneOf(row.source, sources)
    && Array.isArray(row.actions) && row.actions.every(action => oneOf(action, actions))
    && Array.isArray(row.problems) && row.problems.every(problem => typeof problem === "string")
    && (digest === null || (isObject(digest) && typeof digest.pending === "number"
      && Number.isSafeInteger(digest.pending) && digest.pending >= 0 && timestamp(digest.next_at)))
}

function isModesStatus(value: unknown): value is ModesStatus {
  if (!isObject(value) || !isObject(value.schema)) return false
  const { schema, github } = value
  return Array.isArray(schema.modes) && schema.modes.every(mode => oneOf(mode, modes))
    && Array.isArray(schema.dm_modes) && schema.dm_modes.every(mode => mode === "off" || mode === "watch")
    && Array.isArray(schema.actions) && schema.actions.every(action => oneOf(action, actions)) && oneOf(schema.default, modes)
    && Array.isArray(value.spaces) && value.spaces.every(isModeRow)
    && Array.isArray(value.dm) && value.dm.every(entry => isObject(entry) && typeof entry.block === "string"
      && (entry.mode === "off" || entry.mode === "watch") && oneOf(entry.source, sources))
    && (github == null || (isObject(github) && timestamp(github.checked_at)
      && (github.installation === null || (isObject(github.installation)
        && Object.values(github.installation).every(permission => typeof permission === "string")))))
}

export const channelModesQuery = queryOptions({
  queryKey: ["channel-modes"],
  queryFn: ({ signal }) => requestJson("/api/modes", isModesStatus, { signal }),
  staleTime: 15_000,
})

export function modeKey(row: Pick<ModeRow, "block" | "space" | "channel">) {
  return JSON.stringify([row.block, row.space, row.channel])
}

export async function saveChannelMode(row: ModeRow, mode: Mode | "inherit", changes: TriageAction[] | undefined, csrf: string | null) {
  const { block, space, channel } = row
  const changeset = changes === undefined ? {} : { actions: changes }
  const result = await requestJson("/api/modes",
    (value): value is { space: ModeRow } => isObject(value) && isModeRow(value.space) && modeKey(row) === modeKey(value.space),
    { method: "POST", csrf, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block, space, channel, mode, ...changeset }) })
  return result.space
}
