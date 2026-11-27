import { queryOptions } from "@tanstack/react-query"
import { isObject, requestJson } from "@/lib/api"

export const connectionNames = { slack: "Slack", discord: "Discord", telegram: "Telegram" } as const
export type ConnectionPlatform = keyof typeof connectionNames
export type ConnectionState = "connected" | "disconnected" | "not_configured"

export function isConnectionPlatform(value: string | undefined): value is ConnectionPlatform {
  return value !== undefined && Object.hasOwn(connectionNames, value)
}

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
type SlackDetails = { workspaces: { name: string; intake: string; connected: boolean }[] }
type Connection = { state: ConnectionState } & (
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
  const { platform, state, details } = value
  if (platform !== "discord" && platform !== "telegram" && platform !== "slack") return false
  if (state === "not_configured") return details === null
  if (!isObject(details) || (state !== "connected" && state !== "disconnected")) return false
  if (platform === "slack") {
    return Array.isArray(details.workspaces) && details.workspaces.every((workspace: unknown) => isObject(workspace)
      && typeof workspace.name === "string" && typeof workspace.intake === "string" && typeof workspace.connected === "boolean")
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
