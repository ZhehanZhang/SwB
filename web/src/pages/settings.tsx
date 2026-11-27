import { useEffect, useState, type FormEvent } from "react"
import { queryOptions, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { Apple, ChevronRight, KeyRound, LogOut, Monitor, PanelsTopLeft, Smartphone, Terminal, X } from "lucide-react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { PRODUCT_NAME } from "@/app/brand"
import { connectionNames } from "@/app/navigation"
import { preloadOnTouch } from "@/app/route-pages"
import { ConnectionStatus } from "@/components/console/connection-status"
import { Lamp } from "@/components/console/lamp"
import { CopyButton } from "@/components/copy-button"
import { DataState } from "@/components/data-state"
import { LogoTile } from "@/components/logo-tile"
import { ThemePicker } from "@/components/theme-picker"
import { Button } from "@/components/ui/button"
import { ApiError, isObject, markSignedOut, requestJson, sessionQuery, SignedOutError } from "@/lib/api"
import { channelModesQuery, hasGitHubConnection } from "@/lib/channel-modes"
import { companionsQuery, connectionQuery, disconnectCompanion, pairCompanion } from "@/lib/connections"
import { addPasskey, listPasskeys, removePasskey, supportsPasskeys } from "@/lib/owner-auth"

type Settings = {
  version: string | null
  mcp_url: string | null
  data_dir: string
  owner_key_masked: string | null
  host: string
  uptime_seconds: number
}

function isSettings(value: unknown): value is Settings {
  return isObject(value)
    && (value.version === null || typeof value.version === "string")
    && (value.mcp_url === null || typeof value.mcp_url === "string")
    && typeof value.data_dir === "string"
    && (value.owner_key_masked === null || typeof value.owner_key_masked === "string")
    && typeof value.host === "string"
    && typeof value.uptime_seconds === "number" && Number.isSafeInteger(value.uptime_seconds) && value.uptime_seconds >= 0
}

const settingsQuery = queryOptions({
  queryKey: ["settings"],
  queryFn: ({ signal }) => requestJson("/api/settings", isSettings, { signal }),
})

const passkeysQuery = queryOptions({
  queryKey: ["passkeys"],
  queryFn: ({ signal }) => listPasskeys(signal),
})

const commands = ["swb up", "swb status", "swb log"] as const
const rowClass = "grid min-h-[41px] grid-cols-[minmax(88px,24%)_1fr_auto] items-center gap-x-3 border-b text-body-sm"
const headingClass = "text-heading-md"
const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" })
const connectionPlatforms = ["slack", "discord", "telegram"] as const
const deviceIcons = new Map([
  ["darwin", Apple], ["macos", Apple], ["linux", Terminal], ["windows", PanelsTopLeft], ["win32", PanelsTopLeft],
  ["ios", Smartphone], ["android", Smartphone],
])

function DeviceIcon({ platform }: { platform: string }) {
  const Icon = deviceIcons.get(platform.toLowerCase()) ?? Monitor
  return (
    <span role="img" aria-label={platform}
      className="inline-grid size-6 shrink-0 place-items-center rounded-md border border-border bg-surface text-foreground dark:border-border-strong dark:bg-surface-sunken">
      <Icon aria-hidden className="size-3/4" />
    </span>
  )
}

function uptime(seconds: number) {
  if (seconds >= 86_400) return `${Math.floor(seconds / 86_400)} d`
  if (seconds >= 3_600) return `${Math.floor(seconds / 3_600)} h`
  if (seconds >= 60) return `${Math.floor(seconds / 60)} min`
  return `${seconds} s`
}

function date(value: string) {
  const timestamp = Date.parse(value)
  return Number.isNaN(timestamp) ? "Unknown" : dateFormat.format(timestamp)
}

export function SettingsPage() {
  const client = useQueryClient()
  const { data: session } = useQuery(sessionQuery)
  const settings = useQuery(settingsQuery)
  const passkeys = useQuery(passkeysQuery)
  const connections = useQueries({ queries: connectionPlatforms.map(connectionQuery) })
  const modes = useQuery(channelModesQuery)
  const companions = useQuery(companionsQuery)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState("")
  const csrf = session?.csrf ?? null
  const passkeysSupported = supportsPasskeys()
  const endpoint = settings.data?.mcp_url && URL.canParse(settings.data.mcp_url, location.origin)
    ? new URL(settings.data.mcp_url, location.origin).href
    : null

  const refreshPasskeys = () => Promise.all([
    client.invalidateQueries({ queryKey: passkeysQuery.queryKey }),
    client.invalidateQueries({ queryKey: sessionQuery.queryKey }),
  ])
  const { data: pairing, isPending: pairingPending, mutate: pair, reset: clearPairing } = useMutation({
    gcTime: 0,
    mutationFn: () => pairCompanion(csrf),
    onError: (error) => {
      if (!(error instanceof SignedOutError)) toast.error("Pairing code unavailable")
    },
  })
  const disconnect = useMutation({
    mutationFn: (id: string) => disconnectCompanion(id, csrf),
    onSuccess: () => client.invalidateQueries({ queryKey: companionsQuery.queryKey }),
    onError: (error) => {
      if (!(error instanceof SignedOutError)) toast.error("Disconnect failed")
    },
  })
  useEffect(() => {
    if (!pairing) return
    const timeout = window.setTimeout(clearPairing, pairing.expires_in_s * 1_000)
    return () => window.clearTimeout(timeout)
  }, [pairing, clearPairing])

  const register = useMutation({
    mutationFn: () => addPasskey(csrf, name.trim() || "Passkey"),
    onSuccess: async () => {
      setAdding(false)
      setName("")
      await refreshPasskeys()
    },
  })
  const remove = useMutation({
    mutationFn: (id: string) => removePasskey(csrf, id),
    onSuccess: refreshPasskeys,
  })
  const logout = useMutation({
    mutationFn: () => requestJson("/auth/logout", (value): value is undefined => value === undefined, { method: "POST", csrf }),
    onSuccess: () => markSignedOut(client),
    onError: (error) => {
      if (!(error instanceof SignedOutError)) toast.error("Sign-out failed")
    },
  })
  const pending = register.isPending || remove.isPending

  function registerPasskey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    register.mutate()
  }

  return (
    <div className="w-full max-w-[720px] space-y-8 pt-2 text-sm">
      <section aria-labelledby="settings-appearance">
        <h2 id="settings-appearance" className={headingClass}>Appearance</h2>
        <div className="mt-2 flex min-h-[50px] flex-wrap items-center justify-between gap-2 border-b py-2 sm:grid sm:grid-cols-[minmax(88px,24%)_1fr] sm:justify-items-start sm:gap-x-0">
          <span className="text-body-sm text-muted-foreground">Theme</span>
          <ThemePicker />
        </div>
      </section>

      <section aria-labelledby="settings-run">
        <h2 id="settings-run" className={headingClass}>Commands</h2>
        <ul className="mt-5 rounded-lg bg-surface-sunken px-4 py-3">
          {commands.map(command => (
            <li key={command} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2">
              <code className="min-w-0 wrap-break-word text-mono-md">{command}</code>
              <CopyButton text={command} label={`Copy ${command}`} />
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="settings-server">
        <h2 id="settings-server" className={headingClass}>Server</h2>
        {settings.isPending ? <DataState kind="loading" />
          : settings.isError ? <DataState kind="error" message="Server settings unavailable" onRetry={() => void settings.refetch()} />
            : (
              <dl className="mt-2">
                <div className={rowClass}>
                  <dt className="text-muted-foreground">MCP endpoint</dt>
                  <dd className="min-w-0 wrap-anywhere font-mono text-xs">{endpoint}</dd>
                  {endpoint && <CopyButton text={endpoint} label="Copy MCP endpoint" />}
                </div>
                <div className={rowClass}>
                  <dt className="text-muted-foreground">Version</dt>
                  <dd className="font-mono text-xs">{settings.data.version ?? "Unknown"}</dd>
                </div>
                <div className={rowClass}>
                  <dt className="text-muted-foreground">Data</dt>
                  <dd className="min-w-0 wrap-anywhere font-mono text-xs">sqlite, {settings.data.data_dir}</dd>
                </div>
                {settings.data.owner_key_masked && (
                  <div className={rowClass}>
                    <dt className="text-muted-foreground">Owner key</dt>
                    <dd className="font-mono text-xs" aria-label="Owner key configured">{settings.data.owner_key_masked}</dd>
                  </div>
                )}
              </dl>
            )}
      </section>

      <section aria-labelledby="settings-connections">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="settings-connections" className={headingClass}>Connections</h2>
          <Button variant="outline" size="sm" disabled={!csrf || pairingPending || Boolean(pairing)} onClick={() => pair()}>
            {pairingPending ? "Pairing…" : "Pair a device"}
          </Button>
        </div>
        {pairing && (
          <div className="flex flex-wrap items-center justify-end gap-2 border-b py-2">
            <output aria-label="Pairing code" className="wrap-anywhere font-mono text-mono-md">{pairing.code}</output>
            <CopyButton text={pairing.code} label="Copy pairing code" />
            <Button variant="ghost" size="icon" className="size-6 text-muted-foreground" aria-label="Close pairing code" onClick={clearPairing}>
              <X aria-hidden />
            </Button>
          </div>
        )}
        <ul className="mt-2">
          {[...connectionPlatforms, "github" as const].map((platform, index) => {
            const connection = connections[index]
            const state = platform === "github"
              ? modes.isPending ? "loading" : modes.isError ? "unavailable" : hasGitHubConnection(modes.data) ? "connected" : "not_configured"
              : connection.data?.state ?? (connection.isPending ? "loading" : "unavailable")
            return (
              <li key={platform}>
                <Link to={`/connections/${platform}`} {...preloadOnTouch(`/connections/${platform}`)}
                  className="console-pressable group grid min-h-[41px] grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 rounded-sm border-b py-2 text-body-sm">
                  <span className="flex items-center gap-2.5">
                    <LogoTile kind={platform} />
                    <span className="group-hover:underline">{connectionNames[platform]}</span>
                  </span>
                  <ConnectionStatus state={state} />
                  <ChevronRight aria-hidden className="size-3.5" />
                </Link>
              </li>
            )
          })}
          {companions.data?.companions.map(companion => (
            <li key={companion.id}
              className="grid min-h-[41px] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-b py-2 text-body-sm sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <span className="flex min-w-0 items-center gap-2.5">
                <DeviceIcon platform={companion.platform} />
                <span className="min-w-0 wrap-anywhere">{companion.name}</span>
                <Lamp state={companion.online ? "on" : "off"} label={companion.online ? "Online" : "Offline"} />
              </span>
              <span className="order-last col-span-2 wrap-anywhere ps-[34px] text-muted-foreground sm:order-none sm:col-span-1 sm:ps-0">
                {companion.capabilities.join(", ")}
              </span>
              <Button variant="ghost" size="sm" className="text-muted-foreground" disabled={!csrf || disconnect.isPending}
                aria-label={`Disconnect ${companion.name}`} onClick={() => disconnect.mutate(companion.id)}>
                {disconnect.isPending && disconnect.variables === companion.id ? "Disconnecting…" : "Disconnect"}
              </Button>
            </li>
          ))}
        </ul>
        {companions.isError && !companions.data && (
          <DataState kind="error" message="Devices unavailable" onRetry={() => void companions.refetch()} />
        )}
      </section>

      {settings.data && (
        <section aria-labelledby="settings-hosts">
          <h2 id="settings-hosts" className={headingClass}>Hosts</h2>
          <table className="mt-1 w-full table-fixed border-collapse text-left text-[13px]">
            <thead>
              <tr className="border-b text-xs text-muted-foreground">
                <th scope="col" className="w-[78%] py-2 font-normal">Host</th>
                <th scope="col" className="py-2 font-normal">Uptime</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b">
                <td className="py-3 pe-2 wrap-anywhere font-mono text-xs">{settings.data.host}</td>
                <td className="py-3 font-mono text-xs">
                  <div className="flex items-center justify-between gap-1">
                    {uptime(settings.data.uptime_seconds)}
                    <span className="size-2 shrink-0 rounded-full bg-primary" role="img" aria-label="Online" />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      )}

      <section aria-labelledby="settings-sign-in">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="settings-sign-in" className={headingClass}>Sign-in</h2>
          {passkeys.data && !adding && (
            <Button size="sm" variant="outline" disabled={!passkeysSupported || !csrf || pending} onClick={() => {
              register.reset()
              remove.reset()
              setAdding(true)
            }}>
              <KeyRound aria-hidden /> Add a passkey
            </Button>
          )}
        </div>
        {passkeys.isPending ? <DataState kind="loading" message="Loading passkeys…" />
          : passkeys.isError ? <DataState kind="error" message="Passkeys unavailable" onRetry={() => void passkeys.refetch()} />
            : !passkeys.data?.length ? null
                : (
                  <ul className="mt-2">
                    {passkeys.data.map((passkey) => (
                      <li key={passkey.id} className="flex min-h-[41px] flex-wrap items-center justify-between gap-2 border-b">
                        <div className="min-w-0">
                          <div className="wrap-anywhere font-medium">{passkey.name}</div>
                          <div className="text-xs text-muted-foreground">
                            {passkey.last_used_at ? `Last used ${date(passkey.last_used_at)}` : `Added ${date(passkey.created_at)}`}
                          </div>
                        </div>
                        <Button
                          size="sm" variant="ghost" className="text-muted-foreground"
                          disabled={!csrf || pending} aria-label={`Remove ${passkey.name}`}
                          onClick={() => remove.mutate(passkey.id)}
                        >
                          {remove.isPending && remove.variables === passkey.id ? "Removing…" : "Remove"}
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
        {remove.isError && (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {remove.error instanceof ApiError && remove.error.code === "last_method"
              ? "Add a sign-in method first."
              : "Passkey removal failed"}
          </p>
        )}
        {adding && (
          <form onSubmit={registerPasskey} className="mt-3 flex flex-wrap items-end gap-2">
            <label className="block w-full text-label-sm sm:w-60">
              Passkey name
              <input
                value={name} onChange={(event) => setName(event.currentTarget.value)} placeholder="Passkey"
                className="mt-1 block h-9 w-full rounded-md border border-input px-3 text-sm"
              />
            </label>
            <Button variant="ghost" size="sm" disabled={pending} onClick={() => setAdding(false)}>Cancel</Button>
            <Button type="submit" size="sm" disabled={pending || !csrf}>{register.isPending ? "Adding…" : "Add passkey"}</Button>
            {register.isError && <p role="alert" className="w-full text-sm text-destructive">Passkey setup failed</p>}
          </form>
        )}
        {session?.via === "access" ? (
          <Button asChild variant="ghost" size="sm" className="mt-2 -ml-3">
            <a href="/cdn-cgi/access/logout"><LogOut aria-hidden /> Sign out</a>
          </Button>
        ) : session?.signed_in && (
          <Button variant="ghost" size="sm" className="mt-2 -ml-3" disabled={!csrf || logout.isPending} onClick={() => logout.mutate()}>
            <LogOut aria-hidden /> Sign out
          </Button>
        )}
      </section>

      <section aria-labelledby="settings-about">
        <h2 id="settings-about" className={headingClass}>About</h2>
        <Link className="mt-2 flex min-h-[41px] items-center justify-between gap-3 border-b py-2 text-body-sm hover:underline" to="/about">
          About {PRODUCT_NAME}
          <ChevronRight aria-hidden className="size-3.5 shrink-0" />
        </Link>
      </section>
    </div>
  )
}
