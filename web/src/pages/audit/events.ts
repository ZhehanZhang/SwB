import { connectorKind } from "../../lib/connector-kind.js"
import { displayReference } from "../../lib/display-reference.js"

type AuditMetadata = {
  agent?: string
  state?: string
  to_scope?: string
  memory_id?: string
  target_name?: string
  target_label?: string
  target_avatar_url?: string | null
  ref_label?: string
  repo_count?: number
  turn_cap?: number
  visibility?: string
  reason?: string
}

export type AuditEvent = {
  id: number
  at: string | null
  origin: string
  source: string
  destination: string
  action: string
  outcome: string
  target: string | null
  ref: string | null
  project?: string | null
  metadata?: AuditMetadata
}

export type AuditPageData = { items: AuditEvent[]; next_cursor: number | null }

export const auditGroups = {
  All: [],
  Routes: ["route_set", "route_remove", "auto_link", "auto_route", "visibility_set"],
  Tokens: ["token_claim", "routine_claim", "connect_code", "connect_redeem", "connect_revoke", "register", "routine_register", "routine_add", "routine_remove", "add", "remove"],
  Memory: ["memory_write", "memory_delete", "memory_move", "memory_promote", "cross_read"],
  Messages: ["agent_send", "send", "delegate", "fire", "reply", "post", "react", "request_write", "listen_note", "turn_cap", "conversation_resume", "cancel", "webhook", "send_refused", "send_key_fallback"],
} as const

export type AuditGroup = keyof typeof auditGroups

export function isAuditGroup(value: string): value is AuditGroup {
  return Object.hasOwn(auditGroups, value)
}

export function isAuditPage(value: unknown): value is AuditPageData {
  return typeof value === "object" && value !== null && "items" in value && Array.isArray(value.items)
    && "next_cursor" in value && (value.next_cursor === null || Number.isSafeInteger(value.next_cursor))
    && value.items.every((item: unknown) => typeof item === "object" && item !== null
      && "id" in item && Number.isSafeInteger(item.id)
      && "at" in item && (item.at === null || typeof item.at === "string")
      && "source" in item && typeof item.source === "string"
      && "origin" in item && typeof item.origin === "string"
      && "action" in item && typeof item.action === "string"
      && "destination" in item && typeof item.destination === "string"
      && "outcome" in item && typeof item.outcome === "string"
      && "target" in item && (item.target === null || typeof item.target === "string")
      && "ref" in item && (item.ref === null || typeof item.ref === "string")
      && (!("project" in item) || item.project === null || typeof item.project === "string")
      && (!("metadata" in item) || isAuditMetadata(item.metadata)))
}

function isAuditMetadata(value: unknown): value is AuditMetadata {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const metadata = value as Record<string, unknown>
  return ["agent", "state", "to_scope", "memory_id", "target_name", "target_label", "ref_label", "visibility", "reason"].every((key) => !(key in metadata) || typeof metadata[key] === "string")
    && (metadata.target_avatar_url == null || typeof metadata.target_avatar_url === "string")
    && ["repo_count", "turn_cap"].every((key) => !(key in metadata) || Number.isSafeInteger(metadata[key]))
}

export function auditProject({ action, target, project }: AuditEvent): string | null {
  if (project) return project
  if (!target) return null
  if (action === "route_set" || action === "route_remove" || action === "auto_route") {
    return target.match(/^[^/]+\/([a-z0-9-]+)$/)?.[1] ?? null
  }
  if (action === "auto_link" || action === "project_create" || action === "project_update") return target
  return target.match(/^project:([a-z0-9-]+)$/)?.[1] ?? null
}

export function auditActor({ source }: AuditEvent) {
  if (source === "owner" || source.startsWith("admin:")) return "you"
  if (source === "auto") return "SwB"
  return !source || source === "-" ? null : displayReference(source)
}

export function auditActorKind({ origin, source }: AuditEvent) {
  return connectorKind(origin) ?? source
}

const titles: Record<string, readonly [past: string, present: string]> = {
  route_set: ["Set route", "set route"],
  route_remove: ["Removed route", "remove route"],
  auto_link: ["Linked project", "link project"],
  auto_route: ["Routed automatically", "route automatically"],
  visibility_set: ["Changed visibility for", "change visibility for"],
  token_claim: ["Saved token", "save token"],
  connect_code: ["Created pairing code for", "create pairing code for"],
  connect_redeem: ["Paired client", "pair client"],
  connect_revoke: ["Revoked key", "revoke key"],
  register: ["Registered", "register"],
  routine_register: ["Registered routine", "register routine"],
  routine_add: ["Added routine", "add routine"],
  routine_claim: ["Saved routine token for", "save routine token for"],
  routine_remove: ["Removed routine", "remove routine"],
  add: ["Added routine", "add routine"],
  remove: ["Removed routine", "remove routine"],
  project_create: ["Created project", "create project"],
  project_update: ["Updated project", "update project"],
  memory_write: ["Wrote memory", "write memory"],
  memory_delete: ["Deleted memory", "delete memory"],
  memory_move: ["Moved memory", "move memory"],
  memory_promote: ["Promoted memory", "promote memory"],
  cross_read: ["Read memory across projects", "read memory across projects"],
  agent_send: ["Sent message to", "send message to"],
  send: ["Sent message to", "send message to"],
  delegate: ["Delegated to", "delegate to"],
  fire: ["Messaged", "message"],
  reply: ["Replied to", "reply to"],
  post: ["Posted in", "post in"],
  react: ["Reacted in", "react in"],
  request_write: ["Asked to write in", "request a write in"],
  listen_note: ["Notified the owner", "notify the owner"],
  turn_cap: ["Paused conversation", "pause conversation"],
  conversation_resume: ["Resumed conversation", "resume conversation"],
  cancel: ["Stopped following", "stop following"],
  send_refused: ["Blocked message to", "send message to"],
  send_key_fallback: ["Used fallback key for", "use fallback key for"],
  auth_failure: ["Rejected credentials", "authenticate"],
  auth_missing: ["Required credentials", "authenticate request for"],
  oauth_refused: ["Refused OAuth request", "authorize"],
  rate_limited: ["Blocked request at rate limit", "rate limited"],
}

export function auditText(item: AuditEvent): string {
  return displayReference(auditSentence(item))
}

function auditSentence({ action, target, ref, destination, outcome, metadata, project }: AuditEvent): string {
  const memoryPath = action.startsWith("memory_")
    ? target?.match(/^(?:general|project:[a-z0-9-]+)\/(?:[a-z0-9_.-]+\/)+([a-z0-9_.-]+)$/)
    : null
  target = metadata?.target_label || memoryPath?.[1] || (target && displayReference(target))
  ref = metadata?.ref_label || (ref && displayReference(ref))
  if (action === "route_set" && outcome === "ok" && metadata?.target_name) {
    const via = metadata.agent === "github" ? "GitHub" : metadata.agent === "slack" ? "Slack" : metadata.agent || project || "default"
    const count = metadata.repo_count
    const routed = count ? `${count} ${via} ${count === 1 ? "repository" : "repositories"}` : via
    return `Routed ${routed} to ${displayReference(metadata.target_name, "Captain")}`
  }
  if (action === "turn_cap") return ["Paused conversation", ref, metadata?.turn_cap ? `at ${metadata.turn_cap} turns` : null].filter(Boolean).join(" ")
  if (action === "webhook") {
    const label = [outcome === "dropped" ? "Dropped webhook for" : "Received webhook for", target || destination].join(" ")
    const reason = metadata?.reason
    if (!reason) return label
    return `${label}. ${reason === "not completed" ? "Event is pending" : reason.charAt(0).toUpperCase() + reason.slice(1)}.`
  }
  if (action === "auth_failure" || action === "auth_missing" || action === "oauth_refused" || action === "rate_limited") {
    return [titles[action][0], target ? `for ${target}` : null].filter(Boolean).join(" ")
  }
  if (outcome === "queued" && (action === "post" || action === "react" || action === "request_write")) {
    return ["Requested approval", target ? `for ${target}` : null].filter(Boolean).join(" ")
  }
  let subject = target
  if (action === "route_set" || action === "route_remove") {
    subject = [metadata?.agent ? `for ${metadata.agent}` : null, project ? `in ${project}` : null, `via ${destination}`].filter(Boolean).join(" ")
  } else if (action === "memory_move" || action === "memory_promote") {
    subject = [metadata?.memory_id, metadata?.to_scope ? `to ${metadata.to_scope}` : target].filter(Boolean).join(" ")
  } else if (action === "visibility_set" && metadata?.visibility) {
    subject = [target, `to ${metadata.visibility}`].filter(Boolean).join(" ")
  } else if (action === "listen_note") {
    subject = null
  } else if (action === "conversation_resume") {
    subject ||= ref
  }
  const words = Object.hasOwn(titles, action) ? titles[action] : undefined
  if (!words) {
    const label = action.replace(/_/g, " ")
    return [label.charAt(0).toUpperCase() + label.slice(1), subject || destination, outcome === "ok" ? null : `(${outcome})`]
      .filter(Boolean).join(" ")
  }
  const [past, present] = words
  const result = outcome === "refused" ? `Refused to ${present}`
    : outcome === "error" || outcome === "failed" ? `Failed to ${present}`
    : outcome === "duplicate" ? `Skipped duplicate attempt to ${present}`
    : past
  return [
    subject ? result : result.replace(/ (?:to|for|in)$/, ""),
    subject,
    metadata?.state ? `(${metadata.state})` : outcome === "queued" ? "(queued)" : null,
  ].filter(Boolean).join(" ")
}
