import { requestJson } from "@/lib/api"
import { connectorKind } from "@/lib/connector-kind"
import { displayReference } from "@/lib/display-reference"
import type { AuditEvent } from "../audit/events"

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
type AgentKind = "routine" | "captain" | "dot" | "key" | "pending"

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

export type AgentSetup = {
  chatgpt: { connector_url: string | null; plugins_url: string; instruction: string }
  any: { prompt: string }
  coding_agents: { id: string; snippet: string; where: string }[]
}
export type AgentDetail = { keys: Key[]; routines: Routine[]; audit: AuditEvent[] }

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
    && fields(row, ["name", "agent", "block", "status"], ["project", "created_by", "created_at", "last_seen_at"])
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

export function isAgentSetup(value: unknown): value is AgentSetup {
  return isRecord(value) && isRecord(value.chatgpt) && (value.chatgpt.connector_url === null || typeof value.chatgpt.connector_url === "string")
    && value.chatgpt.plugins_url === "https://chatgpt.com/plugins" && typeof value.chatgpt.instruction === "string"
    && isRecord(value.any) && typeof value.any.prompt === "string" && Array.isArray(value.coding_agents)
    && value.coding_agents.every((row: unknown) => isRecord(row) && typeof row.id === "string" && typeof row.snippet === "string" && typeof row.where === "string")
}

export function isAgentDetail(value: unknown): value is AgentDetail {
  return isRecord(value) && Array.isArray(value.keys) && value.keys.every(isKey)
    && Array.isArray(value.routines) && value.routines.every(isRoutine) && Array.isArray(value.audit)
}

function principal(value?: string | null): string | null {
  const actor = value?.trim()
  if (actor === "owner" || actor === "admin" || actor?.startsWith("admin:") || actor?.startsWith("owner:")) return "you"
  return actor?.replace(/^(oauth|key|agent):/, "") || null
}

const clientNames: Record<string, string> = {
  claude: "Claude", "claude-code": "Claude Code", codex: "Codex", chatgpt: "ChatGPT", gemini: "Gemini", "gemini-cli": "Gemini CLI",
  grok: "Grok", xai: "xAI",
}

export const principalLabel = (value: string) => Object.hasOwn(clientNames, value) ? clientNames[value] : value

export const agentListKey = ["registered-agents"]

export async function listAgents(signal: AbortSignal): Promise<Agent[]> {
  const { keys, routines, captains, dots, pending_claims: pending } = await requestJson("/api/agents", isInventory, { signal })
  return [
    ...routines.map((row): Agent => ({
      id: `routine:${row.block}:${row.name}`, reference: row.name, block: row.block, type: "routine", name: row.name, kind: "routine",
      logo: row.agent === "grok" ? "grok-routine" : row.agent, projects: row.project ? [row.project] : [],
      registered_by: principal(row.created_by), registered_at: row.created_at ?? null,
      last_seen: row.last_seen_at ?? null, state: row.status.startsWith("tested") ? "on" : row.status === "untested" ? "off" : "fault",
    })),
    ...(["captain", "dot"] as const).flatMap((kind) => (kind === "captain" ? captains : dots).map((row): Agent => ({
      id: `${kind}:${row.block}:${row.id}`, reference: row.id, block: row.block, type: kind,
      name: kind === "captain" ? displayReference(row.name, "Captain") : row.name, kind,
      logo: kind === "captain" ? "captain" : "chatgpt", projects: row.projects ?? (row.project ? [row.project] : []),
      avatar_url: row.avatar_url,
      registered_by: principal(row.created_by), registered_at: row.created_at ?? null,
      last_seen: row.last_seen_at ?? null, state: row.online ? "on" : "off",
    }))),
    ...keys.map((row): Agent => ({
      id: `key:${row.id}`, reference: row.id, type: "key", name: principalLabel(row.agent), kind: "MCP client",
      logo: connectorKind(row.agent) ?? (Object.hasOwn(clientNames, row.agent) ? row.agent : "mcp"),
      projects: row.project ? [row.project] : [],
      registered_by: principal(row.created_by), registered_at: row.created_at ?? null, last_seen: row.last_used_at ?? null,
      state: row.last_used_at ? "on" : "off",
    })),
    ...pending.map((row): Agent => ({
      id: `pending:${row.name}`, reference: row.name, type: "pending", name: row.name, kind: "routine", logo: "claude",
      projects: [row.project], registered_by: principal(row.created_by), registered_at: row.created_at,
      last_seen: null, state: "off", claim_url: row.claim_url,
    })),
  ]
}
