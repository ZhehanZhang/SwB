import { displayReference } from "../../lib/display-reference.js"

export type PendingWrite = {
  id: string
  workspace: string
  identity: string
  action: string
  key_id: string
  sender?: Sender
  created_at: string | null
  expires_at: string | null
  channel?: string | null
  channel_name?: string | null
  persona?: string | null
  text?: string | null
  name?: string | null
  thread_ts?: string | null
}

export type Pause = { root: string; turns: number; paused_at: string | null; conversation: Conversation | null }

export type Conversation = {
  handle: string
  owner: string
  destination: string
  destination_kind?: string | null
  source_kind?: string | null
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
  target_kind?: string | null
  target_avatar_url?: string | null
  unread?: boolean
  owner_notify?: { surface?: string | null; notified_at?: string | null; reason?: string | null } | null
}

export const deliveryStatuses = {
  stored: "Stored",
  notified: "Notified",
  notify_failed: "Notification failed",
  no_owner_surface: "Owner surface needed",
}

export function ownerDelivery(row: Conversation) {
  if (row.owner !== "owner" || row.destination !== "mailbox") return null
  if (row.status && Object.hasOwn(deliveryStatuses, row.status)) return deliveryStatuses[row.status as keyof typeof deliveryStatuses]
  return row.status === "delivered" ? "Stored" : null
}

export type Sender = {
  kind?: string | null
  avatar_url?: string | null
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
  block?: string | null
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
  count?: number | null
  from?: Sender
}

export type InboxData = {
  pending_writes: PendingWrite[]
  turn_cap: number | null
  pauses: Pause[]
  conversations: Conversation[]
  next_cursor: string | null
}
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
    && (value.sender === undefined || isSender(value.sender))
}

function isPause(value: unknown): value is Pause {
  return isRecord(value) && typeof value.root === "string" && Number.isSafeInteger(value.turns)
    && nullableStrings(value, ["paused_at"])
    && (value.conversation === null || isConversation(value.conversation))
}

function isConversation(value: unknown): value is Conversation {
  return isRecord(value) && strings(value, ["handle", "owner", "destination", "ref"])
    && nullableStrings(value, ["project", "title", "target", "status", "created_at", "updated_at"]) && typeof value.open === "boolean"
    && optionalStrings(value, ["first_target", "destination_kind", "source_kind", "target_kind", "target_avatar_url"])
    && (value.first_sender == null || isSender(value.first_sender))
    && (value.unread === undefined || typeof value.unread === "boolean")
    && (value.owner_notify == null || (isRecord(value.owner_notify) && optionalStrings(value.owner_notify, ["surface", "notified_at", "reason"])))
}

function isSender(value: unknown): value is Sender {
  return isRecord(value) && optionalStrings(value, [
    "kind", "avatar_url", "name", "label", "agent", "trust", "workspace", "channel", "channel_name", "user", "user_name",
    "thread_id", "thread_ts", "ts", "message_id", "author", "title", "dot", "block",
  ])
}

function isItem(value: unknown): value is ConversationItem {
  return isRecord(value) && Number.isSafeInteger(value.cursor) && typeof value.kind === "string" && nullableStrings(value, ["at"])
    && (value.handle === undefined || typeof value.handle === "string")
    && optionalStrings(value, ["text", "status", "in_reply_to", "trust", "project", "target", "target_kind", "event_id", "slack_kind"])
    && (value.final == null || typeof value.final === "boolean")
    && (value.count == null || Number.isSafeInteger(value.count))
    && (value.from === undefined || isSender(value.from))
}

export function isInbox(value: unknown): value is InboxData {
  return isRecord(value)
    && Array.isArray(value.pending_writes) && value.pending_writes.every(isPendingWrite)
    && (value.turn_cap === null || Number.isSafeInteger(value.turn_cap))
    && Array.isArray(value.pauses) && value.pauses.every(isPause)
    && Array.isArray(value.conversations) && value.conversations.every(isConversation)
    && nullableStrings(value, ["next_cursor"])
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
  const targetAgent = row.target?.match(/^agent:(.+)$/)?.[1]
  const targetKind = row.target_kind === "agent" ? targetAgent || target : row.target_kind || targetAgent
  const fromKind = sender?.kind === "watch" ? row.source_kind || senderLogoKind(sender) : senderLogoKind(sender)
  if (row.destination === "mailbox") {
    return {
      from: principalLabel(senderLabel(sender) || (sender?.kind === "slack" && channel ? channelLabel(channel) : row.ref)),
      to: principalLabel(target || row.owner),
      fromKind: fromKind ?? row.ref,
      toKind: targetKind ?? undefined,
    }
  }
  if (destination === "slack") {
    return { from: principalLabel(senderLabel(sender) || (channel ? channelLabel(channel) : row.ref)),
      to: principalLabel(target || row.owner), fromKind: "slack", toKind: targetKind || undefined }
  }
  return {
    from: principalLabel(senderLabel(sender) || row.owner),
    to: principalLabel(target || row.ref || row.destination),
    fromKind: fromKind ?? row.owner,
    toKind: targetKind || destination,
  }
}

export function senderLogoKind(sender?: Sender) {
  if (sender?.kind === "watch") return sender.block === "slack" || sender.block === "discord" || sender.block === "telegram" ? sender.block : "github"
  return (sender?.kind === "agent" ? sender.agent : sender?.kind) || undefined
}

export function senderLabel(sender?: Sender) {
  if (sender?.agent?.startsWith("admin:")) return "you"
  return sender?.name || sender?.user_name || sender?.author || sender?.label || sender?.agent || sender?.title || sender?.user || sender?.dot || null
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
