import { queryOptions } from "@tanstack/react-query"
import { isObject, requestJson } from "./api"

type Settings = {
  version: string | null
  mcp_url: string | null
  deploy_platform?: string | null
  public_url?: string | null
  data_dir: string
  owner_key_masked: string | null
  host: string
  uptime_seconds: number
}

function isSettings(value: unknown): value is Settings {
  return isObject(value)
    && (value.version === null || typeof value.version === "string")
    && (value.mcp_url === null || typeof value.mcp_url === "string")
    && (value.deploy_platform == null || typeof value.deploy_platform === "string")
    && (value.public_url == null || typeof value.public_url === "string")
    && typeof value.data_dir === "string"
    && (value.owner_key_masked === null || typeof value.owner_key_masked === "string")
    && typeof value.host === "string"
    && typeof value.uptime_seconds === "number" && Number.isSafeInteger(value.uptime_seconds) && value.uptime_seconds >= 0
}

export const settingsQuery = queryOptions({
  queryKey: ["settings"],
  queryFn: ({ signal }) => requestJson("/api/settings", isSettings, { signal }),
})
