import { useQuery } from "@tanstack/react-query"
import { ChevronRight, Copy } from "lucide-react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { PRODUCT_NAME } from "@/app/brand"
import { Lamp } from "@/components/console/lamp"
import { DataState } from "@/components/data-state"
import { LogoTile } from "@/components/logo-tile"
import { Button } from "@/components/ui/button"
import { boardQuery, formatDay, quantity, type Board } from "@/pages/overview/data"

type Project = Board["projects"][number]

function Fan({ count, side, compact = false }: { count: number; side: "left" | "right"; compact?: boolean }) {
  if (!count) return <div aria-hidden />
  const step = compact ? 32 : 40
  const height = count * step
  const center = height / 2
  const start = side === "left" ? 0 : 100
  const end = side === "left" ? 100 : 0
  const strokeClass = side === "left" ? "stroke-cord-intake fill-cord-intake" : "stroke-cord-route fill-cord-route"
  return (
    <svg
      aria-hidden
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
      className={`block w-full self-center overflow-visible ${strokeClass}`}
      style={{ height }}
    >
      {Array.from({ length: count }, (_, i) => {
        const y = step * (i + 0.5)
        return (
          <g key={i}>
            <path
              d={`M ${start},${y} C 52,${y} 48,${center} ${end},${center}`}
              fill="none"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            <path
              d={`M ${start},${y} h ${side === "left" ? 0.01 : -0.01}`}
              fill="none"
              strokeLinecap="round"
              strokeWidth={compact ? "6" : "8"}
              vectorEffect="non-scaling-stroke"
            />
          </g>
        )
      })}
    </svg>
  )
}

function ProjectSummary({ project }: { project: Project }) {
  return (
    <Link
      to={`/projects/${encodeURIComponent(project.slug)}`}
      className="group flex min-h-[170px] min-w-0 flex-col self-center rounded-lg bg-muted px-4 py-3.5 hover:bg-border"
    >
      <div className="mb-3.5 flex items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-heading-md">{project.name}</h2>
          <span className="block truncate font-mono text-mono-sm text-subtle-foreground">{project.slug}</span>
        </div>
        <ChevronRight aria-hidden className="size-4 shrink-0 text-subtle-foreground group-hover:text-foreground" />
      </div>
      <dl className="mt-auto space-y-1.5 text-body-sm">
        {[
          ["Turn cap", project.turn_cap ?? "Off"],
          ["Memory", quantity(project.memory_count, "note")],
          ["Open", quantity(project.open_count, "conversation")],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between gap-2">
            <dt className="text-muted-foreground" title={label === "Turn cap" ? "Global limit per conversation" : undefined}>{label}</dt>
            <dd className="text-right font-mono text-mono-sm">{value}</dd>
          </div>
        ))}
      </dl>
    </Link>
  )
}

function SourceList({ project, compact = false }: { project: Project; compact?: boolean }) {
  if (!project.sources.length) return <p className="self-center pr-3 text-right text-body-sm text-muted-foreground">No channels</p>
  return (
    <ul className="min-w-0 self-center">
      {project.sources.map((item) => (
        <li
          key={item.id}
          className={`flex min-w-0 items-center justify-end gap-2 font-mono text-mono-md ${compact ? "h-8 pr-3" : "h-10 pr-5"}`}
          title={item.detail ?? undefined}
        >
          <LogoTile kind={item.kind} className={compact ? "size-[18px]" : undefined} />
          <span className="truncate">{compact ? item.short_label ?? item.label : item.label}</span>
        </li>
      ))}
    </ul>
  )
}

function AgentList({ project, compact = false }: { project: Project; compact?: boolean }) {
  if (!project.agents.length) return <p className="self-center pl-3 text-body-sm text-muted-foreground">No agents</p>
  return (
    <ul className="min-w-0 self-center">
      {project.agents.map((agent) => (
        <li key={agent.id} className={`flex min-w-0 items-center ${compact ? "h-8 gap-1.5 pl-3" : "h-10 gap-2.5 pl-5"}`}>
          <LogoTile kind={agent.kind} className={compact ? "size-[18px]" : undefined} />
          <span className="truncate text-label-sm" title={agent.label}>{agent.label}</span>
          {!compact && (
            <>
              <span className="shrink-0 text-body-sm text-muted-foreground">{agent.role}</span>
              <span className="ml-auto truncate text-right text-body-sm text-muted-foreground">
                {(agent.by || agent.at) && (
                  <>
                    {agent.by && `by ${agent.by.label}`}{agent.by && agent.at && ", "}
                    {agent.at && <time dateTime={agent.at}>{formatDay(agent.at)}</time>}
                  </>
                )}
              </span>
              <span className="flex w-2.5 shrink-0 justify-center">
                {agent.state && <Lamp state={agent.state} label={agent.state === "on" ? "Available" : agent.state === "fault" ? "Failed" : "Unavailable or unverified"} />}
              </span>
            </>
          )}
        </li>
      ))}
    </ul>
  )
}

function ProjectRow({ project }: { project: Project }) {
  return (
    <li className="border-b border-border">
      <div className="hidden grid-cols-[minmax(0,19%)_minmax(36px,7.25%)_minmax(173px,220px)_minmax(36px,7.25%)_minmax(0,1fr)] py-6 lg:grid">
        <SourceList project={project} />
        <Fan count={project.sources.length} side="left" />
        <ProjectSummary project={project} />
        <Fan count={project.agents.length} side="right" />
        <AgentList project={project} />
      </div>
      <div className="space-y-2 py-4 lg:hidden">
        <Link
          to={`/projects/${encodeURIComponent(project.slug)}`}
          className="flex min-h-8 min-w-0 items-center gap-2 rounded-sm"
        >
          <h2 className="truncate text-heading-md">{project.name}</h2>
          <span className="truncate font-mono text-mono-sm text-subtle-foreground">{project.slug}</span>
          <span className="ml-auto shrink-0 text-body-sm text-muted-foreground">{project.open_count} open</span>
          <ChevronRight aria-hidden className="size-4 shrink-0 text-subtle-foreground" />
        </Link>
        {(project.sources.length > 0 || project.agents.length > 0) && (
          <div className="relative grid grid-cols-[minmax(0,1fr)_clamp(24px,10.5vw,42px)_clamp(24px,10.5vw,42px)_minmax(0,1fr)]">
            <SourceList project={project} compact />
            <Fan count={project.sources.length} side="left" compact />
            <Fan count={project.agents.length} side="right" compact />
            <AgentList project={project} compact />
            <span aria-hidden className="absolute top-1/2 left-1/2 size-2.5 -translate-1/2 rounded-full bg-foreground" />
          </div>
        )}
        {!project.sources.length && !project.agents.length && <p className="text-body-sm text-muted-foreground">No line yet</p>}
      </div>
    </li>
  )
}

function Runtime({ runtime }: { runtime: Board["runtime"] }) {
  const url = runtime.mcp_url ? new URL(runtime.mcp_url, location.origin) : null
  const seconds = runtime.uptime_seconds
  const uptime = seconds >= 86_400 ? quantity(Math.floor(seconds / 86_400), "day")
    : seconds >= 3_600 ? quantity(Math.floor(seconds / 3_600), "hour")
    : seconds >= 60 ? quantity(Math.floor(seconds / 60), "minute")
    : quantity(seconds, "second")
  const copyUrl = async () => {
    if (!url) return
    try {
      await navigator.clipboard.writeText(url.href)
      toast.success("MCP URL copied")
    } catch {
      toast.error("Couldn’t copy the MCP URL")
    }
  }
  return (
    <div className="flex min-h-9 flex-wrap items-center justify-between gap-x-5 gap-y-2">
      <div className="flex min-w-0 flex-1 items-center gap-3.5">
        <Lamp state={runtime.online ? "on" : "off"} label={runtime.online ? "MCP available" : "MCP unavailable"} />
        {url ? (
          <span className="truncate font-mono text-mono-md" title={url.href}>{url.host}{url.pathname}</span>
        ) : (
          <span className="text-body-sm text-muted-foreground">{runtime.online ? "MCP URL unavailable" : "MCP not configured"}</span>
        )}
        {url && (
          <Button variant="ghost" onClick={() => void copyUrl()} className="ml-auto sm:ml-0" aria-label="Copy MCP URL">
            <Copy aria-hidden /><span className="hidden sm:inline">Copy</span>
          </Button>
        )}
      </div>
      <div className="hidden min-w-0 items-center gap-3 font-mono text-mono-sm text-subtle-foreground lg:flex">
        {runtime.version && <span>{PRODUCT_NAME} {runtime.version}</span>}
        <span className="truncate" title={runtime.host}>{runtime.host}</span>
        <span>up {uptime}</span>
      </div>
    </div>
  )
}

export function OverviewPage() {
  const board = useQuery(boardQuery)
  if (board.isPending) return <DataState kind="loading" message={`Loading ${PRODUCT_NAME}…`} />
  if (board.isError) return <DataState kind="error" message={`Couldn’t load ${PRODUCT_NAME}.`} onRetry={() => void board.refetch()} />

  return (
    <div>
      <Runtime runtime={board.data.runtime} />
      {board.data.projects.length > 0 ? (
        <ul className="mt-3 lg:mt-5" aria-label="Projects">
          {board.data.projects.map((project) => <ProjectRow key={project.slug} project={project} />)}
        </ul>
      ) : (
        <DataState kind="empty" message="No projects yet." />
      )}
      {board.data.unassigned.length > 0 && (
        <section className="flex flex-wrap items-center gap-4 py-5 text-body-sm text-muted-foreground" aria-labelledby="unassigned-heading">
          <h2 id="unassigned-heading">No line yet</h2>
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {board.data.unassigned.map((item) => (
              <li key={item.id} className="flex items-center gap-2" title={item.detail ?? undefined}>
                <LogoTile kind={item.kind} className="size-[22px]" />
                <span>{item.short_label ?? item.label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
