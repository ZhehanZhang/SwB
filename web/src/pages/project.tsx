import { useQuery } from "@tanstack/react-query"
import { ChevronRight, Copy, ScrollText } from "lucide-react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { PRODUCT_NAME } from "@/app/brand"
import { usePageTitle } from "@/app/page-title"
import { ChannelModeTable } from "@/components/console/channel-mode-table"
import { Lamp } from "@/components/console/lamp"
import { DataState } from "@/components/data-state"
import { LogoTile } from "@/components/logo-tile"
import { Button } from "@/components/ui/button"
import { channelModesQuery } from "@/lib/channel-modes"
import { displayReference } from "@/lib/display-reference"
import {
  coordinatorQuery, formatAge, formatDay, formatTime, projectBoardQuery, quantity,
  type Endpoint, type ProjectBoard, type ProjectRoute,
} from "@/pages/overview/data"

function CopyPrompt() {
  const prompt = useQuery(coordinatorQuery)
  const copy = async () => {
    if (!prompt.data) {
      const result = await prompt.refetch()
      if (result.isError) toast.error("Try again")
      return
    }
    try {
      await navigator.clipboard.writeText(prompt.data)
      toast.success("Coordinator prompt copied")
    } catch {
      toast.error("Try again")
    }
  }
  return (
    <Button variant="outline" size="sm" disabled={prompt.isPending || prompt.isFetching} onClick={() => void copy()}>
      <Copy aria-hidden /> {prompt.isError ? "Reload coordinator prompt" : "Copy coordinator prompt"}
    </Button>
  )
}

function ProjectFacts({ data }: { data: ProjectBoard }) {
  const project = data.project
  const facts = [
    { label: "Turn cap", value: project.turn_cap ?? "Off" },
    { label: "Memory", value: quantity(project.memory_count, "note") },
    ...(project.created_at ? [{ label: "Created", value: <time dateTime={project.created_at}>{formatDay(project.created_at)}</time> }] : []),
  ]
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 lg:min-h-9">
      <div className="flex w-full min-w-0 flex-wrap items-center gap-x-8 gap-y-2 lg:w-auto">
        <nav aria-label="Breadcrumb" className="hidden items-center gap-2 text-body-sm lg:flex">
          <Link to="/" className="rounded-sm text-muted-foreground hover:underline">{PRODUCT_NAME}</Link>
          <ChevronRight aria-hidden className="size-3.5 text-subtle-foreground" />
          <span aria-current="page" className="font-mono text-mono-sm">{project.slug}</span>
        </nav>
        <dl className="flex w-full flex-col gap-x-8 gap-y-1.5 text-body-sm lg:w-auto lg:flex-row">
          {facts.map(({ label, value }) => (
            <div key={label} className="flex justify-between gap-2">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-mono text-mono-sm">{value}</dd>
            </div>
          ))}
        </dl>
        {project.archived && <span className="text-body-sm text-muted-foreground">Archived</span>}
      </div>
      <CopyPrompt />
    </div>
  )
}

function RoutePoint({ endpoint, mode, short = false }: {
  endpoint: Endpoint
  mode: "intake" | "route" | "sink"
  short?: boolean
}) {
  const color = { intake: "bg-cord-intake", route: "bg-cord-route", sink: "bg-cord-sink" }[mode]
  return (
    <span className="flex min-w-0 items-center gap-2" title={endpoint.detail ?? endpoint.label}>
      <span aria-hidden className={`size-1.5 shrink-0 rounded-full ${color}`} />
      <LogoTile kind={endpoint.kind} avatarUrl={endpoint.avatar_url ?? null} className="size-[21px]" />
      <span className={`truncate ${mode !== "route" && "font-mono text-mono-sm"}`}>
        {short ? endpoint.short_label ?? endpoint.label : endpoint.label}
      </span>
    </span>
  )
}

function Actor({ actor }: { actor: Endpoint | null }) {
  if (!actor) return null
  return (
    <span className="inline-flex min-w-0 items-center gap-2">
      {actor.kind !== "owner" && actor.kind !== "config" && <LogoTile kind={actor.kind} className="size-[21px]" />}
      <span className="truncate" title={actor.label}>{actor.label}</span>
    </span>
  )
}

function ProjectRoutes({ routes }: { routes: ProjectRoute[] }) {
  return (
    <section aria-labelledby="project-routes-heading">
      <h2 id="project-routes-heading" className="text-heading-md">Routes</h2>
      {routes.length > 0 && (
        <>
          <table className="mt-1 hidden w-full table-fixed text-left text-body-sm lg:table">
            <thead className="text-muted-foreground">
              <tr className="border-b border-border">
                <th scope="col" className="w-[23.5%] py-2 pr-3 font-normal">From</th>
                <th scope="col" className="w-[38%] py-2 pr-3 font-normal">To</th>
                <th scope="col" className="py-2 pr-3 font-normal">Set by</th>
                <th scope="col" className="w-4"><span className="sr-only">Status</span></th>
              </tr>
            </thead>
            <tbody>
              {routes.map((route) => (
                <tr key={route.id} className="h-[43px] border-b border-border">
                  <td className="py-2 pr-3"><RoutePoint endpoint={route.from} mode={route.direction === "reply" ? "route" : "intake"} /></td>
                  <td className="py-2 pr-3" title={route.delivery ?? undefined}>
                    <div className="flex items-center gap-2">
                      <RoutePoint endpoint={route.to} mode={route.direction === "reply" ? "sink" : "route"} />
                      {route.direction === "reply" && <span className="shrink-0 text-muted-foreground">Thread reply</span>}
                    </div>
                  </td>
                  <td className="py-2 pr-3">
                    <span className="flex items-center">
                      <Actor actor={route.by} />
                      {route.at && <time dateTime={route.at} className="shrink-0">{route.by && ", "}{formatTime(route.at)}</time>}
                    </span>
                  </td>
                  <td className="text-right">{route.state && <Lamp state={route.state} label={route.state === "on" ? "Available" : route.state === "fault" ? "Failed" : "Unavailable or unverified"} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="mt-1 lg:hidden">
            {routes.map((route) => (
              <li key={route.id} className="space-y-1 border-b border-border py-2.5" title={route.delivery ?? undefined}>
                <div className="flex items-center gap-2">
                  <RoutePoint endpoint={route.from} mode={route.direction === "reply" ? "route" : "intake"} short />
                  <ChevronRight aria-hidden className="size-3.5 shrink-0 text-subtle-foreground" />
                  <RoutePoint endpoint={route.to} mode={route.direction === "reply" ? "sink" : "route"} short />
                </div>
                {(route.by || route.at || route.direction === "reply") && (
                  <p className="text-body-sm text-muted-foreground">
                    {route.direction === "reply" && "Thread reply"}
                    {route.by && `${route.direction === "reply" ? ", " : ""}by ${route.by.label}`}
                    {route.at && <time dateTime={route.at}>{(route.by || route.direction === "reply") && ", "}{formatDay(route.at)}</time>}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function ProjectHistory({ data }: { data: ProjectBoard }) {
  const search = new URLSearchParams({ project: data.project.slug })
  function summarize(row: ProjectBoard["history"][number]) {
    const action = ({
      route_set: "Set route",
      route_remove: "Removed route",
      connect_redeem: "Connected MCP client",
      delegate: "Delegated work",
      reply: "Replied",
    })[row.action]
      ?? row.action.replaceAll("_", " ")
    const message = `${action.charAt(0).toUpperCase()}${action.slice(1)}`
    return displayReference(`${message}${row.destination === "core" ? "" : ` on ${row.destination}`}${row.outcome === "ok" ? "" : ` (${row.outcome})`}`)
  }
  return (
    <section aria-labelledby="project-history-heading">
      <header className="flex items-center justify-between gap-3">
        <h2 id="project-history-heading" className="text-heading-md">History</h2>
        <Button asChild variant="ghost" size="sm">
          <Link to={`/audit?${search}`}><ScrollText aria-hidden /> Audit</Link>
        </Button>
      </header>
      {data.history.length > 0 && (
        <>
          <table className="mt-1 hidden w-full table-fixed text-left text-body-sm lg:table">
            <thead className="text-muted-foreground">
              <tr className="border-b border-border">
                <th scope="col" className="w-[15.5%] py-2 pr-3 font-normal">When</th>
                <th scope="col" className="w-[23.5%] py-2 pr-3 font-normal">Who</th>
                <th scope="col" className="py-2 font-normal">What</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((row) => (
                <tr key={row.id} className="h-[43px] border-b border-border">
                  <td className="py-2 pr-3 font-mono text-mono-sm text-muted-foreground">{row.at && <time dateTime={row.at}>{formatTime(row.at)}</time>}</td>
                  <td className="py-2 pr-3"><Actor actor={row.by} /></td>
                  <td className="py-2 wrap-anywhere">{summarize(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="mt-1 lg:hidden">
            {data.history.map((row) => (
              <li key={row.id} className="space-y-1 border-b border-border py-2.5">
                <p className="wrap-anywhere">{summarize(row)}</p>
                <p className="flex flex-wrap gap-x-3 font-mono text-mono-sm text-subtle-foreground">
                  {row.at && <time dateTime={row.at}>{formatTime(row.at)}</time>}
                  {row.by && <span>{row.by.label}</span>}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function ProjectActivity({ data }: { data: ProjectBoard }) {
  const search = new URLSearchParams({ scope: data.project.scope })
  return (
    <div className="min-w-0 space-y-8 lg:space-y-9">
      <section aria-labelledby="project-conversations-heading">
        <h2 id="project-conversations-heading" className="text-heading-md">Open conversations</h2>
        {data.conversations.length > 0 && (
          <ul className="mt-2">
            {data.conversations.map(({ handle, endpoint, title, at }) => (
              <li key={handle} className="border-b border-border">
                <Link to={`/inbox/${encodeURIComponent(handle)}`} className="block py-2.5 hover:bg-muted">
                  <div className="flex min-w-0 items-center gap-2">
                    <LogoTile kind={endpoint.kind} avatarUrl={endpoint.avatar_url ?? null} className="size-[18px]" />
                    <span className="truncate text-label-sm">{endpoint.label}</span>
                    {at && <time title={formatTime(at)} dateTime={at} className="ml-auto shrink-0 font-mono text-mono-sm text-subtle-foreground">{formatAge(at)}</time>}
                  </div>
                  <div className="mt-0.5 truncate text-body-sm text-muted-foreground">{displayReference(title || handle)}</div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="project-memory-heading">
        <header className="flex items-center justify-between gap-3">
          <h2 id="project-memory-heading" className="text-heading-md">Memory</h2>
          <Button asChild variant="ghost" size="sm">
            <Link to={`/memory?${search}`}><ChevronRight aria-hidden /> All {data.project.memory_count}</Link>
          </Button>
        </header>
        {data.memory.length > 0 && (
          <ul className="mt-1.5">
            {data.memory.map((note) => (
              <li key={note.id} className="border-b border-border">
                <Link
                  to={`/memory?${search}&${new URLSearchParams({ id: note.id })}`}
                  className="flex items-center justify-between gap-4 py-2.5 hover:bg-muted"
                  title={note.title}
                >
                  <span className="truncate font-mono text-mono-sm">{note.key}</span>
                  <span className="truncate text-body-sm text-muted-foreground">
                    {note.by && note.by.label}{note.by && note.updated_at && ", "}
                    {note.updated_at && <time dateTime={note.updated_at}>{formatDay(note.updated_at)}</time>}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

function ProjectModes({ slug }: { slug: string }) {
  const result = useQuery(channelModesQuery)
  if (result.isPending) return <DataState kind="loading" />
  if (result.isError) return <DataState kind="error" message="Channel modes unavailable" onRetry={() => void result.refetch()} />
  const rows = result.data?.spaces.filter(row => row.project === slug) ?? []
  if (!result.data || rows.length === 0) return <DataState kind="empty" message="Channels appear here when assigned to this project." />
  return <ChannelModeTable label="Channel modes" rows={rows} status={result.data} />
}

export function ProjectPage() {
  const { slug = "" } = useParams()
  const [search, setSearch] = useSearchParams()
  const showModes = search.get("tab") === "modes"
  const board = useQuery(projectBoardQuery(slug))
  usePageTitle(board.data?.project.name)
  if (board.isPending) return <DataState kind="loading" message="Loading project…" />
  if (board.isError) return <DataState kind="error" message="Project unavailable" onRetry={() => void board.refetch()} />
  const data = board.data
  return (
    <div>
      <ProjectFacts data={data} />
      <nav aria-label="Project views" className="mt-5 flex border-b">
        {([["overview", "Overview"], ["modes", "Channel modes"]] as const).map(([tab, label]) => {
          const selected = showModes === (tab === "modes")
          return <button key={tab} type="button" aria-current={selected ? "page" : undefined}
            className={`border-b-2 px-3 py-2 text-body-sm ${selected ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}
            onClick={() => setSearch(previous => {
              const next = new URLSearchParams(previous)
              if (tab === "modes") next.set("tab", "modes")
              else next.delete("tab")
              return next
            })}>{label}</button>
        })}
      </nav>
      {showModes ? <ProjectModes slug={data.project.slug} /> : <div className="mt-7 grid min-w-0 gap-x-12 gap-y-8 lg:mt-10 lg:grid-cols-[minmax(0,2.44fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-8 lg:space-y-9">
          <ProjectRoutes routes={data.routes} />
          <ProjectHistory data={data} />
        </div>
        <ProjectActivity data={data} />
      </div>}
    </div>
  )
}
