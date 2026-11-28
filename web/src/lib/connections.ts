import { queryOptions } from "@tanstack/react-query"
import type { ConnectionPlatform } from "@/app/navigation"
import { isObject, requestJson } from "@/lib/api"

export type ConnectionState = "connected" | "disconnected" | "not_configured"
export type StatusPlatform = Exclude<ConnectionPlatform, "github">

export type BotAvatar = {
  file: string | null
  state: "applied" | "failed" | "pending" | "not_configured"
  url: string | null
}

type Bot = { bot_name: string | null; owner_ids: string[]; avatar: BotAvatar }
type DiscordChannel = { id: string; name: string | null; project: string | null; listening: boolean }
type DiscordGuild = {
  id: string
  name: string | null
  owner_id: string | null
  space: string | null
  channels: DiscordChannel[]
}
export type DiscordDetails = Bot & { invite_url: string | null; guilds: DiscordGuild[] }
type TelegramChat = {
  id: string
  title: string | null
  type: string | null
  space: string | null
  listen: "direct" | "listening" | "unlisted"
}
export type TelegramDetails = Bot & { chats: TelegramChat[] }
type SlackDetails = { workspaces: { name: string; intake: "socket" | "poll" | "none"; connected: boolean }[] }
type GitHubDetails = { repositories: string[]; credential: "app" | "pat" | null; dropped: number; refused: number }
export type Webhook = {
  id: string
  name: string
  verify: string
  events: number
  refused: number
  last_event_at: string | null
  secret: "set" | "waiting"
} & (
  | { direction: "in"; to: string; url: string }
  | { direction: "out"; address: string }
)
type Connection = { state: ConnectionState; block: string | null } & (
  | { platform: "discord"; details: DiscordDetails | null }
  | { platform: "telegram"; details: TelegramDetails | null }
  | { platform: "slack"; details: SlackDetails | null }
  | { platform: "github"; details: GitHubDetails | null }
  | { platform: "webhook"; details: { webhooks: Webhook[] } | null }
)

const nullableString = (value: unknown) => value === null || typeof value === "string"

function isBot(value: Record<string, unknown>, platform: "discord" | "telegram") {
  const avatar = value.avatar
  return nullableString(value.bot_name) && Array.isArray(value.owner_ids)
    && value.owner_ids.every((id: unknown) => typeof id === "string")
    && isObject(avatar) && nullableString(avatar.file)
    && ["applied", "failed", "pending", "not_configured"].some(state => state === avatar.state)
    && (avatar.url === null || avatar.url === `/api/connections/${platform}/avatar`)
}

function isWebhook(value: unknown): value is Webhook {
  if (!isObject(value)) return false
  const { id, name, verify, direction, events, refused, last_event_at, secret } = value
  return typeof id === "string" && typeof name === "string" && typeof verify === "string"
    && typeof events === "number" && typeof refused === "number"
    && (last_event_at === null || (typeof last_event_at === "string" && Number.isFinite(Date.parse(last_event_at))))
    && (secret === "set" || secret === "waiting")
    && (direction === "in" ? typeof value.to === "string" && typeof value.url === "string"
      : direction === "out" && typeof value.address === "string" && !Object.hasOwn(value, "url"))
}

function isConnection(value: unknown): value is Connection {
  if (!isObject(value)) return false
  const { platform, state, details, block } = value
  if (platform !== "discord" && platform !== "telegram" && platform !== "slack" && platform !== "github" && platform !== "webhook") return false
  if (state === "not_configured") return details === null && block === null
  if (typeof block !== "string" || !isObject(details) || (state !== "connected" && state !== "disconnected")) return false
  if (platform === "webhook") {
    return Array.isArray(details.webhooks) && details.webhooks.every(isWebhook)
  }
  if (platform === "github") {
    return Array.isArray(details.repositories) && details.repositories.every((repo: unknown) => typeof repo === "string")
      && (details.credential === null || details.credential === "app" || details.credential === "pat")
      && typeof details.dropped === "number" && typeof details.refused === "number"
  }
  if (platform === "slack") {
    return Array.isArray(details.workspaces) && details.workspaces.every((workspace: unknown) => isObject(workspace)
      && typeof workspace.name === "string" && typeof workspace.connected === "boolean"
      && (workspace.intake === "socket" || workspace.intake === "poll" || workspace.intake === "none"))
  }
  if (!isBot(details, platform)) return false
  if (platform === "discord") {
    return (details.invite_url === null || (typeof details.invite_url === "string" && details.invite_url.startsWith("https://discord.com/oauth2/authorize?")))
      && Array.isArray(details.guilds) && details.guilds.every((guild: unknown) => isObject(guild)
        && typeof guild.id === "string" && nullableString(guild.name) && nullableString(guild.space) && nullableString(guild.owner_id)
        && Array.isArray(guild.channels) && guild.channels.every((channel: unknown) => isObject(channel)
          && typeof channel.id === "string" && nullableString(channel.name) && nullableString(channel.project) && typeof channel.listening === "boolean"))
  }
  return Array.isArray(details.chats) && details.chats.every((chat: unknown) => isObject(chat)
    && typeof chat.id === "string" && nullableString(chat.title) && nullableString(chat.type) && nullableString(chat.space)
    && (chat.listen === "direct" || chat.listen === "listening" || chat.listen === "unlisted"))
}

export function connectionQuery(platform: ConnectionPlatform) {
  return queryOptions({
    queryKey: ["connection", platform],
    queryFn: ({ signal }) => requestJson(`/api/connections/${platform}`,
      (value): value is Connection => isConnection(value) && value.platform === platform, { signal }),
  })
}

export type Companion = {
  id: string
  name: string
  platform: string
  online: boolean
  capabilities: string[]
}

function isCompanion(value: unknown): value is Companion {
  return isObject(value) && typeof value.id === "string" && typeof value.name === "string"
    && typeof value.platform === "string" && typeof value.online === "boolean"
    && Array.isArray(value.capabilities) && value.capabilities.every(capability => typeof capability === "string")
}

export const companionsQuery = queryOptions({
  queryKey: ["companions"],
  queryFn: ({ signal }) => requestJson("/api/companions",
    (value): value is { companions: Companion[] } => isObject(value)
      && Array.isArray(value.companions) && value.companions.every(isCompanion), { signal }),
  refetchInterval: 15_000,
})

export async function pairCompanion(csrf: string | null) {
  const { code, expires_in_s } = await requestJson("/pairing/companion",
    (value): value is { code: string; expires_in_s: number } => isObject(value)
      && typeof value.code === "string" && value.code.length > 0
      && typeof value.expires_in_s === "number" && Number.isSafeInteger(value.expires_in_s) && value.expires_in_s > 0,
    { method: "POST", csrf })
  return { code, expires_in_s }
}

export function disconnectCompanion(id: string, csrf: string | null) {
  return requestJson(`/api/companions/${encodeURIComponent(id)}/revoke`,
    (value): value is { ok: true } => isObject(value) && value.ok === true, { method: "POST", csrf })
}
