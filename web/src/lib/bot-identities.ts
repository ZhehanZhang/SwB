import { queryOptions } from "@tanstack/react-query"
import { isObject, requestJson } from "@/lib/api"

export type IdentityPlatform = "discord" | "slack" | "telegram"
export type BotIdentity = {
  name: string
  icon_url: string
  can_edit: boolean
  can_edit_icon?: boolean
  permissions_url: string | null
  overridden: boolean
  post_every_agent?: boolean
}
type SpaceIdentity = { id: string; name: string; identity: BotIdentity }
type BotIdentities = { block: string; platform: IdentityPlatform; identity: BotIdentity; spaces: SpaceIdentity[] }
export type IdentityChange = { space?: string } & ({ name?: string; icon?: string; post_every_agent?: boolean } | { reset: true })

const pathFor = (block: string) => `/api/bot-identities/${encodeURIComponent(block)}`
const botIcon = /^\/avatars\/(?:swb|bot-[0-9a-f]{48})\.png$/

function isPermissionsUrl(value: unknown) {
  if (value === null) return true
  if (typeof value !== "string") return false
  try {
    const url = new URL(value)
    return url.protocol === "https:" && !url.username && !url.password
  } catch {
    return false
  }
}

function isIdentity(value: unknown, platform: IdentityPlatform): value is BotIdentity {
  return isObject(value) && typeof value.name === "string"
    && typeof value.icon_url === "string"
    && (botIcon.test(value.icon_url) || value.icon_url === `/api/connections/${platform}/avatar`)
    && typeof value.can_edit === "boolean" && typeof value.overridden === "boolean"
    && (value.can_edit_icon === undefined || typeof value.can_edit_icon === "boolean")
    && (value.post_every_agent === undefined || typeof value.post_every_agent === "boolean")
    && isPermissionsUrl(value.permissions_url)
}

function snapshot(block: string, platform: IdentityPlatform) {
  return (value: unknown): value is BotIdentities => isObject(value) && value.block === block && value.platform === platform
    && isIdentity(value.identity, platform) && Array.isArray(value.spaces)
    && value.spaces.every((space: unknown) => isObject(space) && typeof space.id === "string"
      && typeof space.name === "string" && isIdentity(space.identity, platform))
}

export function botIdentitiesQuery(block: string, platform: IdentityPlatform) {
  return queryOptions({
    queryKey: ["bot-identities", block, platform],
    queryFn: ({ signal }) => requestJson(pathFor(block), snapshot(block, platform), { signal }),
    retry: false,
  })
}

export function saveBotIdentity(block: string, platform: IdentityPlatform, csrf: string, changes: IdentityChange) {
  return requestJson(pathFor(block), snapshot(block, platform), {
    csrf,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  })
}
