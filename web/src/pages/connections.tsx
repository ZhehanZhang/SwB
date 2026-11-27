import { useLayoutEffect, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronLeft, ExternalLink } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { connectionNames, isConnectionPlatform, type ConnectionPlatform } from "@/app/navigation"
import { ChannelModeRow } from "@/components/console/channel-mode-row"
import { ChannelModeTable } from "@/components/console/channel-mode-table"
import { ConnectionStatus } from "@/components/console/connection-status"
import { CopyButton } from "@/components/copy-button"
import { DataState } from "@/components/data-state"
import { LogoTile } from "@/components/logo-tile"
import { Button } from "@/components/ui/button"
import { channelModesQuery, hasGitHubConnection, type ModeRow, type ModesStatus } from "@/lib/channel-modes"
import { connectionQuery, type BotAvatar, type DiscordDetails, type StatusPlatform, type TelegramDetails } from "@/lib/connections"
import { cn } from "@/lib/utils"

const cell = "py-3 pe-3 align-top wrap-anywhere"
const identityRow = "grid min-h-[41px] grid-cols-[minmax(74px,25%)_minmax(0,1fr)] items-center gap-3 border-b py-2.5"
const avatarLabels = { applied: "Applied", failed: "Update failed", pending: "Pending", not_configured: "" }
const chatStates = { direct: "Direct messages", listening: "Listening", unlisted: "Unlisted" }
const slackIntake = { socket: "Socket Mode", poll: "Polling", none: "" }

function StatusTable({ label, headings, children, compact = false }: {
  label: string; headings: string[]; children: ReactNode; compact?: boolean
}) {
  const widths = headings.length === 4 ? ["w-[30%]", "w-[25%]", "w-[20%]"]
    : [cn("w-[36%] md:w-2/5"), cn("w-[26%] md:w-auto")]
  return (
    <table aria-label={label} className="mt-1 w-full table-fixed border-collapse text-left">
      <thead className={compact ? "hidden md:table-header-group" : undefined}>
        <tr className="border-b text-caption text-muted-foreground">
          {headings.map((heading, index) => (
            <th key={heading} scope="col" className={`py-2 pe-2 font-normal ${widths[index] ?? ""}`}>{heading}</th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  )
}

function ChannelRow({ name, type, mapping, mode, current, status }: {
  name: ReactNode; type?: string; mapping: string | null; mode: string; current?: ModeRow; status?: ModesStatus | null
}) {
  const columns = type ? 4 : 3
  const desktopCell = cn(cell, "hidden md:table-cell")
  const leading = (
    <>
      <td className={desktopCell}>{name}</td>
      {type && <td className={`${desktopCell} text-muted-foreground`}>{type}</td>}
      <td className={`${desktopCell} font-mono text-mono-sm text-muted-foreground`}>{mapping}</td>
    </>
  )
  return current && status ? (
    <ChannelModeRow row={current} status={status} label={name} mapping={mapping} columns={columns}>{leading}</ChannelModeRow>
  ) : (
    <tr className="border-b">
      {leading}
      <td className={`${desktopCell} text-muted-foreground`}>{mode}</td>
      <td colSpan={columns} className="py-3 md:hidden">
        <div className="flex items-center justify-between gap-3">
          <span className="min-w-0 wrap-anywhere text-label-sm">{name}</span>
          <span className="shrink-0 text-muted-foreground">{mode}</span>
        </div>
        {(mapping || type) && (
          <div className="mt-1 flex flex-wrap gap-x-3 text-caption text-muted-foreground">
            {mapping && <span className="wrap-anywhere font-mono">{mapping}</span>}
            {type && <span>{type}</span>}
          </div>
        )}
      </td>
    </tr>
  )
}

function DiscordServers({ guilds, rows, status }: { guilds: DiscordDetails["guilds"]; rows: ModeRow[]; status?: ModesStatus | null }) {
  return (
    <section aria-labelledby="discord-servers">
      <h2 id="discord-servers" className="text-heading-md">Servers</h2>
      {guilds.length > 0 && (
        <div className="mt-4 space-y-6">
          {guilds.map(guild => (
            <section key={guild.id} aria-labelledby={`discord-server-${guild.id}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <h3 id={`discord-server-${guild.id}`} className="wrap-anywhere text-label-sm">{guild.name ?? guild.id}</h3>
                {guild.owner_id && (
                  <span className="text-caption text-muted-foreground">Owner <span className="font-mono">{guild.owner_id}</span></span>
                )}
              </div>
              {guild.channels.length > 0 && (
                <StatusTable label={`Channels in ${guild.name ?? guild.id}`} headings={["Channel", "Space or project", "Mode"]} compact>
                  {guild.channels.map(channel => {
                    const current = rows.find(row => row.channel === channel.id && row.space === guild.space)
                    return <ChannelRow key={channel.id} name={channel.name ?? channel.id}
                      mapping={current ? current.project ?? current.space : channel.project ?? guild.space}
                      mode={channel.listening ? "Listening" : "Unlisted"} status={status} current={current} />
                  })}
                </StatusTable>
              )}
            </section>
          ))}
        </div>
      )}
    </section>
  )
}

function TelegramChats({ chats, rows, status }: { chats: TelegramDetails["chats"]; rows: ModeRow[]; status?: ModesStatus | null }) {
  return (
    <section aria-labelledby="telegram-chats">
      <h2 id="telegram-chats" className="text-heading-md">Channels</h2>
      {chats.length > 0 && (
        <StatusTable label="Telegram channels" headings={["Channel", "Type", "Space", "Mode"]} compact>
          {chats.map(chat => (
            <ChannelRow key={chat.id} name={chat.title ?? <span className="font-mono text-mono-sm">{chat.id}</span>}
              type={chat.type ? chat.type.charAt(0).toUpperCase() + chat.type.slice(1) : "Unknown"}
              mapping={chat.space} mode={chatStates[chat.listen]} status={status}
              current={rows.find(row => row.channel === chat.id && row.space === chat.space)} />
          ))}
        </StatusTable>
      )}
    </section>
  )
}

function BotMetadata({ name, ids, avatar, invite }: { name: string; ids: string[]; avatar: BotAvatar; invite?: string | null }) {
  const [imageUnavailable, setImageUnavailable] = useState(false)
  const avatarStatus = imageUnavailable ? "Preview unavailable" : avatarLabels[avatar.state]
  const showAvatar = Boolean(avatar.url || avatarStatus)
  if (!invite && ids.length === 0 && !showAvatar) return null
  return (
    <section aria-labelledby="connection-bot" className="lg:order-last">
      <h2 id="connection-bot" className="text-heading-md">Bot</h2>
      <dl className="mt-1">
        {invite && (
          <div className={identityRow}>
            <dt className="text-muted-foreground">Invite link</dt>
            <dd className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-mono text-mono-sm" title={invite}>{invite.replace(/^https:\/\//, "")}</span>
              <CopyButton text={invite} label="Copy Discord invite link" />
              <Button asChild size="icon" variant="ghost" className="size-6 text-muted-foreground hover:text-foreground">
                <a href={invite} title="Open Discord invite" aria-label="Open Discord invite" target="_blank" rel="noreferrer">
                  <ExternalLink aria-hidden />
                </a>
              </Button>
            </dd>
          </div>
        )}
        {ids.length > 0 && (
          <div className={identityRow}>
            <dt className="text-muted-foreground">{ids.length === 1 ? "Owner ID" : "Owner IDs"}</dt>
            <dd className="space-y-1 wrap-anywhere font-mono text-mono-sm">
              {ids.map(id => <div key={id}>{id}</div>)}
            </dd>
          </div>
        )}
        {showAvatar && (
          <div className={identityRow}>
            <dt className="text-muted-foreground">{name === "Discord" ? "Avatar" : "Photo"}</dt>
            <dd className="flex min-w-0 items-center gap-3">
              {avatar.url && !imageUnavailable && (
                <img src={avatar.url} alt={`${name} bot ${name === "Discord" ? "avatar" : "photo"}`}
                  onError={() => setImageUnavailable(true)} className="size-10 shrink-0 rounded-md border border-border object-cover" />
              )}
              <span>{avatarStatus}</span>
            </dd>
          </div>
        )}
      </dl>
    </section>
  )
}

function ConnectionDetail({ platform }: { platform: StatusPlatform }) {
  const result = useQuery(connectionQuery(platform))
  const modes = useQuery(channelModesQuery)
  const name = connectionNames[platform]
  const data = result.data
  const botName = data?.platform !== "slack" ? data?.details?.bot_name : null
  const rows = modes.data?.spaces.filter(row => row.block === data?.block) ?? []
  const shownChannels = data?.platform === "discord" ? data.details?.guilds.flatMap(guild => guild.channels
    .filter(() => guild.space !== null).map(channel => [guild.space, channel.id]))
    : data?.platform === "telegram" ? data.details?.chats.filter(chat => chat.space !== null).map(chat => [chat.space, chat.id]) : []
  const modeRows = rows.filter(row => !shownChannels?.some(([space, id]) => row.space === space && row.channel === id))

  return (
    <div className="w-full max-w-[1104px] space-y-6 text-body-sm md:space-y-8 md:pt-2">
      <Link to="/settings" className="hidden w-fit items-center gap-1 text-muted-foreground hover:text-foreground md:flex">
        <ChevronLeft aria-hidden className="size-4" /> Settings
      </Link>
      {result.isPending ? <DataState kind="loading" />
        : result.isError ? <DataState kind="error" message={`${name} status unavailable`} onRetry={() => void result.refetch()} />
        : data && (
          <>
            <header className="flex items-center gap-3">
              <LogoTile kind={platform} className="size-10" />
              <div className="min-w-0">
                {botName && <p className="wrap-anywhere text-heading-md">{botName}</p>}
                <ConnectionStatus state={data.state} />
              </div>
            </header>
            {data.platform === "slack" && data.details && (
              <section aria-labelledby="slack-workspaces" className="max-w-[720px]">
                <h2 id="slack-workspaces" className="text-heading-md">Workspaces</h2>
                {data.details.workspaces.length > 0 && (
                  <StatusTable label="Slack workspaces" headings={["Workspace", "Intake", "Connection"]}>
                    {data.details.workspaces.map(workspace => (
                      <tr key={workspace.name} className="border-b">
                        <td className={cell}>{workspace.name}</td>
                        <td className={`${cell} text-muted-foreground`}>{slackIntake[workspace.intake]}</td>
                        <td className="whitespace-nowrap py-3 align-top"><ConnectionStatus state={workspace.connected ? "connected" : "disconnected"} /></td>
                      </tr>
                    ))}
                  </StatusTable>
                )}
              </section>
            )}
            {data.platform !== "slack" && data.details && (
              <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:gap-12">
                <BotMetadata key={platform} name={name} ids={data.details.owner_ids} avatar={data.details.avatar}
                  invite={data.platform === "discord" ? data.details.invite_url : undefined} />
                {data.platform === "discord"
                  ? <DiscordServers guilds={data.details.guilds} rows={rows} status={modes.data} />
                  : <TelegramChats chats={data.details.chats} rows={rows} status={modes.data} />}
              </div>
            )}
            {modes.isError && <DataState kind="error" message="Modes unavailable" onRetry={() => void modes.refetch()} />}
            {modes.data && modeRows.length > 0 && (
              <section aria-labelledby="connection-modes">
                <h2 id="connection-modes" className="text-heading-md">Modes</h2>
                <ChannelModeTable label="Connection modes" rows={modeRows} status={modes.data} />
              </section>
            )}
          </>
        )}
    </div>
  )
}

function GitHubConnection() {
  const modes = useQuery(channelModesQuery)
  return (
    <div className="w-full max-w-[1104px] space-y-6 text-body-sm md:space-y-8 md:pt-2">
      <Link to="/settings" className="hidden w-fit items-center gap-1 text-muted-foreground hover:text-foreground md:flex">
        <ChevronLeft aria-hidden className="size-4" /> Settings
      </Link>
      <header className="flex items-center gap-3">
        <LogoTile kind="github" className="size-10" />
        <ConnectionStatus state={modes.isPending ? "loading" : modes.isError ? "unavailable"
          : hasGitHubConnection(modes.data) ? "connected" : "not_configured"} />
      </header>
      {modes.isError && <DataState kind="error" message="GitHub status unavailable" onRetry={() => void modes.refetch()} />}
      {modes.data && (
        <section aria-labelledby="github-modes">
          <h2 id="github-modes" className="text-heading-md">Modes</h2>
          <ChannelModeTable label="GitHub modes" rows={modes.data.spaces.filter(row => row.kind === "repo")} status={modes.data} />
        </section>
      )}
    </div>
  )
}

export function ConnectionsPage() {
  const { platform } = useParams<{ platform: ConnectionPlatform }>()
  useLayoutEffect(() => { window.scrollTo(0, 0) }, [platform])
  if (!isConnectionPlatform(platform)) return <DataState kind="empty" message="Unknown connection" />
  return platform === "github" ? <GitHubConnection /> : <ConnectionDetail platform={platform} />
}
