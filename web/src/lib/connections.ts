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
type Connection = { state: ConnectionState; block: string | null } & (
  | { platform: "discord"; details: DiscordDetails | null }
  | { platform: "telegram"; details: TelegramDetails | null }
  | { platform: "slack"; details: SlackDetails | null }
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

function isConnection(value: unknown): value is Connection {
  if (!isObject(value)) return false
  const { platform, state, details, block } = value
  if (platform !== "discord" && platform !== "telegram" && platform !== "slack") return false
  if (state === "not_configured") return details === null && block === null
  if (typeof block !== "string" || !isObject(details) || (state !== "connected" && state !== "disconnected")) return false
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

export function connectionQuery(platform: StatusPlatform) {
  return queryOptions({
    queryKey: ["connection", platform],
    queryFn: ({ signal }) => requestJson(`/api/connections/${platform}`,
      (value): value is Connection => isConnection(value) && value.platform === platform, { signal }),
  })
}
