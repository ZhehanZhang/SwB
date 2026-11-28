import { agentLogo, principalLabel } from "../../lib/agent-identity.js"
import { ApiError, requestJson } from "../../lib/api.js"
import { displayReference } from "../../lib/display-reference.js"
import type { AuditEvent } from "../audit/events.js"

export type Key = {
  id: string
  agent: string
  client: string | null
  project: string | null
  created_by: string | null
  created_at: string | null
  last_used_at: string | null
}

type Routine = {
  name: string
  agent: string
  block: string
  project: string | null
  primary: boolean
  status: string
  source?: string
  created_by: string | null
  created_at: string | null
  last_seen_at: string | null
}

type Target = {
  id: string
  name: string
  block: string
  project?: string | null
  projects?: string[]
  online: boolean
  created_by?: string | null
  created_at?: string | null
  last_seen_at?: string | null
  avatar_url?: string | null
}
type Pending = { name: string; project: string; created_by: string; created_at: string; claim_url?: string | null }
type Inventory = { keys: Key[]; routines: Routine[]; captains: Target[]; dots: Target[]; pending_claims: Pending[] }
const KIND_LABEL = {
  routine: "Routine",
  captain: "Captain",
  dot: "Dot",
  key: "MCP client",
  mailbox: "Mailbox",
  pending: "Routine",
} as const
type AgentKind = keyof typeof KIND_LABEL

export type Agent = {
  id: string
  reference: string
  block?: string
  type: AgentKind
  name: string
  kind: string
  logo: string
  avatar_url?: string | null
  projects: string[]
  registered_by: string | null
  registered_at: string | null
  last_seen: string | null
  state: "on" | "off" | "fault"
  claim_url?: string | null
}

export type AgentDetail = { agent: Agent; keys: Key[]; routines: Routine[]; audit: AuditEvent[] }
type AgentDetailResponse = Omit<AgentDetail, "agent"> & (
  | { kind: "routine"; agent: Routine }
  | { kind: "captain"; agent: Target }
  | { kind: "dot"; agent: Target }
  | { kind: "key" | "mailbox"; agent: Key }
)

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function fields(row: Record<string, unknown>, required: string[], optional: string[] = []) {
  return required.every((key) => typeof row[key] === "string")
    && optional.every((key) => row[key] == null || typeof row[key] === "string")
}

function isKey(row: unknown): row is Key {
  return isRecord(row) && fields(row, ["id", "agent"], ["client", "project", "created_by", "created_at", "last_used_at"])
}

function isRoutine(row: unknown): row is Routine {
  return isRecord(row) && typeof row.primary === "boolean"
    && fields(row, ["name", "agent", "block", "status"], ["project", "source", "created_by", "created_at", "last_seen_at"])
}

function isTarget(row: unknown): row is Target {
  return isRecord(row) && typeof row.online === "boolean"
    && fields(row, ["id", "name", "block"], ["project", "created_by", "created_at", "last_seen_at", "avatar_url"])
    && (row.projects === undefined || Array.isArray(row.projects) && row.projects.every((value: unknown) => typeof value === "string"))
}

function isInventory(value: unknown): value is Inventory {
  return isRecord(value) && Array.isArray(value.keys) && value.keys.every(isKey) && Array.isArray(value.routines) && value.routines.every(isRoutine)
    && Array.isArray(value.captains) && value.captains.every(isTarget) && Array.isArray(value.dots) && value.dots.every(isTarget)
    && Array.isArray(value.pending_claims) && value.pending_claims.every((row: unknown) =>
      isRecord(row) && fields(row, ["name", "project", "created_by", "created_at"], ["claim_url"]))
}

function isAgentDetail(value: unknown): value is AgentDetailResponse {
  if (!isRecord(value) || !Array.isArray(value.keys) || !value.keys.every(isKey)
    || !Array.isArray(value.routines) || !value.routines.every(isRoutine) || !Array.isArray(value.audit)) return false
  switch (value.kind) {
    case "routine": return isRoutine(value.agent)
    case "captain":
    case "dot": return isTarget(value.agent)
    case "key":
    case "mailbox": return isKey(value.agent)
    default: return false
  }
}

function principal(value?: string | null): string | null {
  const actor = value?.trim()
  if (actor === "owner" || actor === "admin" || actor?.startsWith("admin:") || actor?.startsWith("owner:")) return "you"
  return actor?.replace(/^(oauth|key|agent):/, "") || null
}

export const agentListKey = ["registered-agents"]

function routineAgent(row: Routine): Agent {
  return {
    id: `routine:${row.block}:${row.name}`, reference: row.name, block: row.block, type: "routine", name: row.name, kind: KIND_LABEL.routine,
    logo: row.agent === "grok" ? "grok-routine" : row.agent, projects: row.project ? [row.project] : [],
    registered_by: principal(row.created_by), registered_at: row.created_at ?? null,
    last_seen: row.last_seen_at ?? null, state: row.status.startsWith("tested") ? "on" : row.status === "untested" ? "off" : "fault",
  }
}

function targetAgent(kind: "captain" | "dot", row: Target): Agent {
  return {
    id: `${kind}:${row.block}:${row.id}`, reference: row.id, block: row.block, type: kind,
    name: kind === "captain" ? displayReference(row.name, "Capy Captain") : row.name, kind: KIND_LABEL[kind],
    logo: kind, projects: row.projects ?? (row.project ? [row.project] : []),
    avatar_url: row.avatar_url,
    registered_by: principal(row.created_by), registered_at: row.created_at ?? null,
    last_seen: row.last_seen_at ?? null, state: row.online ? "on" : "off",
  }
}

function clientAgent(kind: "key" | "mailbox", row: Key): Agent {
  return {
    id: `${kind}:${row.id}`, reference: row.id, type: kind, name: principalLabel(row.agent), kind: KIND_LABEL[kind],
    logo: agentLogo(row.agent),
    projects: row.project ? [row.project] : [],
    registered_by: principal(row.created_by), registered_at: row.created_at ?? null, last_seen: row.last_used_at ?? null,
    state: row.last_used_at ? "on" : "off",
  }
}

export const agentDetailKey = ["agent-detail"]

export async function getAgentDetail(subject: string | Agent, signal: AbortSignal): Promise<AgentDetail | null> {
  let path: string
  if (typeof subject === "string") {
    if (subject === "." || subject === "..") return null
    path = `/api/agents/${encodeURIComponent(subject)}`
  } else {
    const params = new URLSearchParams({ kind: subject.type, id: subject.reference })
    if (subject.block) params.set("block", subject.block)
    path = `/api/agents/detail?${params}`
  }
  try {
    const detail = await requestJson(path, isAgentDetail, { signal })
    const agent = detail.kind === "routine" ? routineAgent(detail.agent)
      : detail.kind === "captain" || detail.kind === "dot" ? targetAgent(detail.kind, detail.agent)
        : clientAgent(detail.kind, detail.agent)
    return { agent, keys: detail.keys, routines: detail.routines, audit: detail.audit }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export async function listAgents(signal: AbortSignal): Promise<Agent[]> {
  const { keys, routines, captains, dots, pending_claims: pending } = await requestJson("/api/agents", isInventory, { signal })
  return [
    ...routines.map(routineAgent),
    ...captains.map((row) => targetAgent("captain", row)),
    ...dots.map((row) => targetAgent("dot", row)),
    ...keys.map((row) => clientAgent("key", row)),
    ...pending.map((row): Agent => ({
      id: `pending:${row.name}`, reference: row.name, type: "pending", name: row.name, kind: KIND_LABEL.pending, logo: "claude",
      projects: [row.project], registered_by: principal(row.created_by), registered_at: row.created_at,
      last_seen: null, state: "off", claim_url: row.claim_url,
    })),
  ]
}
