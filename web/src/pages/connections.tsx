import { useLayoutEffect, useState, type ReactNode } from "react"
import { useQuery } from "@tanstack/react-query"
import { ChevronLeft, ExternalLink } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { connectionNames, isConnectionPlatform, type ConnectionPlatform } from "@/app/navigation"
import { ConnectionStatus } from "@/components/console/connection-status"
import { CopyButton } from "@/components/copy-button"
import { DataState } from "@/components/data-state"
import { LogoTile } from "@/components/logo-tile"
import { Button } from "@/components/ui/button"
import { connectionQuery, type BotAvatar, type DiscordDetails, type TelegramDetails } from "@/lib/connections"

const cell = "py-3 pe-3 align-top wrap-anywhere"
const identityRow = "grid min-h-[41px] grid-cols-[minmax(74px,25%)_minmax(0,1fr)] items-center gap-3 border-b py-2.5"
const avatarLabels = { applied: "Applied", failed: "Couldn’t apply", pending: "Not applied", not_configured: "Not configured" }
const chatStates = { direct: "Direct messages", listening: "Listening", unlisted: "Unlisted" }
const slackIntake = { socket: "Socket Mode", poll: "Polling", none: "None" }

function StatusTable({ label, headings, children }: { label: string; headings: string[]; children: ReactNode }) {
  const widths = headings.length === 4 ? ["w-[30%]", "w-[25%]", "w-[20%]"] : ["w-2/5"]
  return (
    <table aria-label={label} className="mt-1 w-full table-fixed border-collapse text-left">
      <thead>
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

function ChannelRow({ name, type, mapping, mode }: { name: ReactNode; type?: string; mapping: string; mode: string }) {
  return (
    <tr className="border-b">
      <td className={cell}>{name}</td>
      {type && <td className={`${cell} text-muted-foreground`}>{type}</td>}
      <td className={`${cell} font-mono text-mono-sm text-muted-foreground`}>{mapping}</td>
      <td className={`${cell} text-muted-foreground`}>{mode}</td>
    </tr>
  )
}

function DiscordServers({ guilds }: { guilds: DiscordDetails["guilds"] }) {
  return (
    <section aria-labelledby="discord-servers">
      <h2 id="discord-servers" className="text-heading-md">Servers</h2>
      {guilds.length === 0 ? <p className="mt-3 text-muted-foreground">No servers discovered.</p> : (
        <div className="mt-4 space-y-6">
          {guilds.map(guild => (
            <section key={guild.id} aria-labelledby={`discord-server-${guild.id}`}>
              <h3 id={`discord-server-${guild.id}`} className="wrap-anywhere text-label-sm">{guild.name ?? guild.id}</h3>
              {guild.owner_id && (
                <p className="mt-1 text-caption text-muted-foreground">Server owner <span className="font-mono">{guild.owner_id}</span></p>
              )}
              {guild.channels.length === 0 ? <p className="mt-2 text-muted-foreground">No channels discovered.</p> : (
                <StatusTable label={`Channels in ${guild.name ?? guild.id}`} headings={["Channel", "Space or project", "Mode"]}>
                  {guild.channels.map(channel => (
                    <ChannelRow key={channel.id} name={channel.name ?? channel.id} mapping={channel.project ?? guild.space ?? "Unmapped"}
                      mode={channel.listening ? "Listening" : "Unlisted"} />
                  ))}
                </StatusTable>
              )}
            </section>
          ))}
        </div>
      )}
    </section>
  )
}

function TelegramChats({ chats }: { chats: TelegramDetails["chats"] }) {
  return (
    <section aria-labelledby="telegram-chats">
      <h2 id="telegram-chats" className="text-heading-md">Seen chats</h2>
      {chats.length === 0 ? <p className="mt-3 text-muted-foreground">No chats seen.</p> : (
        <StatusTable label="Seen Telegram chats" headings={["Chat", "Type", "Space", "Mode"]}>
          {chats.map(chat => (
            <ChannelRow key={chat.id} name={chat.title ?? <span className="font-mono text-mono-sm">{chat.id}</span>}
              type={chat.type ? chat.type.charAt(0).toUpperCase() + chat.type.slice(1) : "Unknown"}
              mapping={chat.space ?? "Unmapped"} mode={chatStates[chat.listen]} />
          ))}
        </StatusTable>
      )}
    </section>
  )
}

function BotMetadata({ name, ids, avatar, invite }: { name: string; ids: string[]; avatar: BotAvatar; invite?: string | null }) {
  const [imageUnavailable, setImageUnavailable] = useState(false)
  return (
    <section aria-labelledby="connection-bot">
      <h2 id="connection-bot" className="text-heading-md">Bot details</h2>
      <dl className="mt-1">
        {invite !== undefined && (
          <div className={identityRow}>
            <dt className="text-muted-foreground">Invite link</dt>
            <dd className="flex min-w-0 items-center gap-2">
              {invite ? (
                <>
                  <span className="min-w-0 flex-1 truncate font-mono text-mono-sm" title={invite}>{invite.replace(/^https:\/\//, "")}</span>
                  <CopyButton text={invite} label="Copy Discord invite link" />
                  <Button asChild size="icon" variant="ghost" className="size-6 text-muted-foreground hover:text-foreground">
                    <a href={invite} title="Open Discord invite" aria-label="Open Discord invite" target="_blank" rel="noreferrer">
                      <ExternalLink aria-hidden />
                    </a>
                  </Button>
                </>
              ) : <span>Not available</span>}
            </dd>
          </div>
        )}
        <div className={identityRow}>
          <dt className="text-muted-foreground">{ids.length === 1 ? "Owner ID" : "Owner IDs"}</dt>
          <dd className="space-y-1 wrap-anywhere font-mono text-mono-sm">
            {ids.length ? ids.map(id => <div key={id}>{id}</div>) : "Not configured"}
          </dd>
        </div>
        <div className={identityRow}>
          <dt className="text-muted-foreground">{name === "Discord" ? "Avatar" : "Photo"}</dt>
          <dd className="flex min-w-0 items-center gap-3">
            {avatar.url && !imageUnavailable && (
              <img src={avatar.url} alt={`${name} bot ${name === "Discord" ? "avatar" : "photo"}`}
                onError={() => setImageUnavailable(true)} className="size-10 shrink-0 rounded-md border border-border object-cover" />
            )}
            <div className="min-w-0 wrap-anywhere">
              <div>{avatarLabels[avatar.state]}</div>
              {avatar.file && <div className="font-mono text-mono-sm text-muted-foreground">{avatar.file}</div>}
              {imageUnavailable && <div className="text-caption text-muted-foreground">Preview unavailable</div>}
            </div>
          </dd>
        </div>
      </dl>
    </section>
  )
}

function ConnectionDetail({ platform }: { platform: ConnectionPlatform }) {
  const result = useQuery(connectionQuery(platform))
  const name = connectionNames[platform]
  const data = result.data
  const botName = data?.platform !== "slack" ? data?.details?.bot_name : null
  useLayoutEffect(() => { window.scrollTo(0, 0) }, [platform])

  return (
    <div className="w-full max-w-[1104px] space-y-6 text-body-sm md:space-y-8 md:pt-2">
      <Link to="/settings" className="hidden w-fit items-center gap-1 text-muted-foreground hover:text-foreground md:flex">
        <ChevronLeft aria-hidden className="size-4" /> Settings
      </Link>
      {result.isPending ? <DataState kind="loading" />
        : result.isError ? <DataState kind="error" message={`Couldn’t load ${name} status.`} onRetry={() => void result.refetch()} />
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
                {data.details.workspaces.length === 0 ? <p className="mt-3 text-muted-foreground">No workspaces configured.</p> : (
                  <StatusTable label="Slack workspaces" headings={["Workspace", "Intake", "Connection"]}>
                    {data.details.workspaces.map(workspace => (
                      <tr key={workspace.name} className="border-b">
                        <td className={cell}>{workspace.name}</td>
                        <td className={`${cell} text-muted-foreground`}>{slackIntake[workspace.intake]}</td>
                        <td className={cell}><ConnectionStatus state={workspace.connected ? "connected" : "disconnected"} /></td>
                      </tr>
                    ))}
                  </StatusTable>
                )}
              </section>
            )}
            {data.platform !== "slack" && data.details && (
              <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:gap-12">
                <div className="lg:order-last">
                  <BotMetadata key={platform} name={name} ids={data.details.owner_ids} avatar={data.details.avatar}
                    invite={data.platform === "discord" ? data.details.invite_url : undefined} />
                </div>
                {data.platform === "discord" ? <DiscordServers guilds={data.details.guilds} /> : <TelegramChats chats={data.details.chats} />}
              </div>
            )}
          </>
        )}
    </div>
  )
}

export function ConnectionsPage() {
  const { platform } = useParams()
  return isConnectionPlatform(platform) ? <ConnectionDetail platform={platform} />
    : <DataState kind="empty" message="Connection not found." />
}
