import { displayReference } from "../../lib/display-reference.js"

export type PendingWrite = {
  id: string
  workspace: string
  identity: string
  action: string
  key_id: string
  created_at: string | null
  expires_at: string | null
  channel?: string | null
  channel_name?: string | null
  persona?: string | null
  text?: string | null
  name?: string | null
  thread_ts?: string | null
}

export type Pause = { root: string; turns: number; paused_at: string | null }

export type Conversation = {
  handle: string
  owner: string
  destination: string
  destination_kind?: string | null
  ref: string
  project: string | null
  title: string | null
  target: string | null
  status: string | null
  open: boolean
  created_at: string | null
  updated_at: string | null
  first_sender?: Sender | null
  first_target?: string | null
  target_avatar_url?: string | null
}

export type Sender = {
  kind?: string | null
  name?: string | null
  label?: string | null
  agent?: string | null
  trust?: string | null
  workspace?: string | null
  channel?: string | null
  channel_name?: string | null
  user?: string | null
  user_name?: string | null
  thread_id?: string | null
  thread_ts?: string | null
  ts?: string | null
  message_id?: string | null
  author?: string | null
  title?: string | null
  dot?: string | null
}

export type ConversationItem = {
  cursor: number
  handle?: string
  kind: string
  at: string | null
  text?: string | null
  status?: string | null
  final?: boolean | null
  in_reply_to?: string | null
  trust?: string | null
  project?: string | null
  target?: string | null
  target_kind?: string | null
  event_id?: string | null
  slack_kind?: string | null
  from?: Sender
}

type InboxData = { pending_writes: PendingWrite[]; turn_cap: number | null; pauses: Pause[]; conversations: Conversation[] }
type ConversationData = { conversation: Conversation; items: ConversationItem[]; cursor: number }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function strings(value: Record<string, unknown>, fields: string[]) {
  return fields.every((field) => typeof value[field] === "string")
}

function nullableStrings(value: Record<string, unknown>, fields: string[]) {
  return fields.every((field) => value[field] === null || typeof value[field] === "string")
}

function optionalStrings(value: Record<string, unknown>, fields: string[]) {
  return fields.every((field) => !(field in value) || value[field] === null || typeof value[field] === "string")
}

function isPendingWrite(value: unknown): value is PendingWrite {
  return isRecord(value) && strings(value, ["id", "workspace", "identity", "action", "key_id"])
    && nullableStrings(value, ["created_at", "expires_at"])
    && optionalStrings(value, ["channel", "channel_name", "persona", "text", "name", "thread_ts"])
}

function isPause(value: unknown): value is Pause {
  return isRecord(value) && typeof value.root === "string" && Number.isSafeInteger(value.turns)
    && nullableStrings(value, ["paused_at"])
}

function isConversation(value: unknown): value is Conversation {
  return isRecord(value) && strings(value, ["handle", "owner", "destination", "ref"])
    && nullableStrings(value, ["project", "title", "target", "status", "created_at", "updated_at"]) && typeof value.open === "boolean"
    && optionalStrings(value, ["first_target", "destination_kind", "target_avatar_url"])
    && (value.first_sender == null || isSender(value.first_sender))
}

function isSender(value: unknown): value is Sender {
  return isRecord(value) && optionalStrings(value, [
    "kind", "name", "label", "agent", "trust", "workspace", "channel", "channel_name", "user", "user_name",
    "thread_id", "thread_ts", "ts", "message_id", "author", "title", "dot",
  ])
}

function isItem(value: unknown): value is ConversationItem {
  return isRecord(value) && Number.isSafeInteger(value.cursor) && typeof value.kind === "string" && nullableStrings(value, ["at"])
    && (value.handle === undefined || typeof value.handle === "string")
    && optionalStrings(value, ["text", "status", "in_reply_to", "trust", "project", "target", "target_kind", "event_id", "slack_kind"])
    && (value.final == null || typeof value.final === "boolean")
    && (value.from === undefined || isSender(value.from))
}

export function isInbox(value: unknown): value is InboxData {
  return isRecord(value)
    && Array.isArray(value.pending_writes) && value.pending_writes.every(isPendingWrite)
    && (value.turn_cap === null || Number.isSafeInteger(value.turn_cap))
    && Array.isArray(value.pauses) && value.pauses.every(isPause)
    && Array.isArray(value.conversations) && value.conversations.every(isConversation)
}

export function isConversationPage(value: unknown): value is ConversationData {
  return isRecord(value) && isConversation(value.conversation) && Number.isSafeInteger(value.cursor)
    && Array.isArray(value.items) && value.items.every(isItem)
}

export function isWriteDecision(value: unknown): value is { id: string; status: string } {
  return isRecord(value) && strings(value, ["id", "status"])
}

export function isResumed(value: unknown): value is { root: string; resumed: true } {
  return isRecord(value) && typeof value.root === "string" && value.resumed === true
}

export function principalLabel(value: string) {
  if (value.startsWith("admin:")) return "you"
  return displayReference(value.replace(/^agent:/, ""))
}

export function participants(row: Conversation): { from: string; to: string; fromKind?: string; toKind?: string } {
  const sender = row.first_sender ?? undefined
  const target = row.first_target || row.target
  const channel = sender?.channel_name || sender?.channel
  const destination = row.destination_kind || row.destination
  if (row.destination === "mailbox") {
    return {
      from: principalLabel(sender?.kind === "slack" && channel ? channelLabel(channel) : senderLabel(sender) || row.ref),
      to: principalLabel(target || row.owner),
      fromKind: senderLogoKind(sender) ?? row.ref,
      toKind: row.target?.match(/^agent:(.+)$/)?.[1],
    }
  }
  if (destination === "slack") {
    return { from: channel ? channelLabel(channel) : principalLabel(senderLabel(sender) || row.ref), to: principalLabel(target || row.owner), fromKind: "slack" }
  }
  return { from: principalLabel(row.owner), to: principalLabel(target || row.ref || row.destination), fromKind: row.owner, toKind: destination }
}

export function senderLogoKind(sender?: Sender) {
  return (sender?.kind === "agent" ? sender.agent : sender?.kind) || undefined
}

export function senderLabel(sender?: Sender) {
  if (sender?.agent?.startsWith("admin:")) return "you"
  return sender?.user_name || sender?.author || sender?.name || sender?.label || sender?.agent || sender?.title || sender?.user || sender?.dot || null
}

export function channelLabel(channel: string) {
  return /^[CGD][A-Z0-9]{6,}$/.test(channel) || channel.startsWith("#") ? channel : `#${channel}`
}

export function approvalTitle({ action, identity, channel, channel_name, thread_ts }: PendingWrite) {
  const address = channel_name || channel
  const at = address ? ` in ${channelLabel(address)}` : " in Slack"
  const as = identity === "user" ? " as you" : ""
  if (action === "post_message") return `${thread_ts ? "Reply" : "Post"}${as}${at}`
  if (action === "add_reaction") return `React${as}${at}`
  return `Slack request${address ? at : ""}`
}
