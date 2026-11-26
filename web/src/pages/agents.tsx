import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Copy, ExternalLink } from "lucide-react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { toast } from "sonner"
import { Lamp } from "@/components/console/lamp"
import { DataState, type DataStateProps } from "@/components/data-state"
import { Logo } from "@/components/logo"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/tabs"
import { Button } from "@/components/ui/button"
import { Sheet } from "@/components/ui/sheet"
import { requestJson, sessionQuery } from "@/lib/api"
import { auditText } from "./audit/events"
import { agentListKey, isAgentDetail, isAgentSetup, listAgents, type Agent, type AgentDetail } from "./agents/data"

const connections = [
  { id: "claude-code", label: "Claude Code", preview: "claude mcp add …" },
  { id: "codex", label: "Codex", preview: "MCP config" },
  { id: "chatgpt", label: "ChatGPT", preview: "Connector URL" },
  { id: "gemini-cli", label: "Gemini CLI", preview: "MCP config" },
  { id: "any", label: "Any agent", preview: "Setup prompt" },
]

const detailPath = (id: string) => `/agents/${encodeURIComponent(id)}`
const date = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" })
const shortDate = (value: string | null) => value ? date.format(new Date(value)) : "—"

function elapsed(value: string | null) {
  const timestamp = value ? Date.parse(value) : NaN
  if (!Number.isFinite(timestamp)) return null
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000))
  if (minutes < 1) return "Now"
  if (minutes < 60) return `${minutes} min`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h`
  return `${Math.floor(minutes / 1440)} d`
}

function AgentLamp({ state }: { state: Agent["state"] }) {
  return <Lamp state={state} label={state === "on" ? "Active" : state === "fault" ? "Needs attention" : "Inactive"} />
}

function ConnectionRow() {
  const query = useQuery({
    queryKey: ["agent-connection-snippets"],
    queryFn: ({ signal }) => requestJson("/api/agents/setup", isAgentSetup, { signal }),
  })
  async function copy(snippet: string) {
    try {
      await navigator.clipboard.writeText(snippet)
      toast.success("Copied")
    } catch {
      toast.error("Couldn’t copy")
    }
  }

  return (
    <section className="border-b pb-3" aria-labelledby="connect-agent">
      <h2 id="connect-agent" className="text-base leading-6 font-semibold tracking-[-0.01em]">Connect an agent</h2>
      {query.isPending ? <DataState kind="loading" message="Loading setup snippets…" /> : query.isError ? (
        <DataState kind="error" message="Couldn’t load setup snippets." onRetry={() => void query.refetch()} />
      ) : (
        <div className="mt-4 grid gap-x-7 gap-y-4 md:flex md:flex-wrap">
          {connections.map(({ id, label, preview }) => {
            const coding = query.data.coding_agents.find((entry) => entry.id === id)
            const snippet = id === "chatgpt" ? query.data.chatgpt.connector_url : id === "any" ? query.data.any.prompt : coding?.snippet
            const firstLine = snippet?.split("\n", 1)[0]
            const monospace = coding ? (firstLine === "{" ? coding.where : firstLine) ?? preview : preview
            return (
              <div key={id} className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium"><Logo kind={id} />{label}</div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <code title={monospace} className="max-w-36 truncate font-mono text-xs leading-4 text-muted-foreground">{monospace}</code>
                  <Button aria-label={`Copy ${label} setup`} title="Copy" variant="ghost" size="icon" className="size-6" disabled={!snippet} onClick={() => snippet && void copy(snippet)}>
                    <Copy aria-hidden strokeWidth={1.5} />
                  </Button>
                  {id === "chatgpt" && <>
                    <a href={query.data.chatgpt.plugins_url} target="_blank" rel="noreferrer" aria-label="Add a custom MCP server in ChatGPT Plugins" aria-describedby="chatgpt-setup" title={query.data.chatgpt.instruction} className="inline-flex items-center gap-1 rounded-sm text-xs text-muted-foreground hover:underline">
                      Plugins <ExternalLink aria-hidden className="size-3" strokeWidth={1.5} />
                    </a>
                    <span id="chatgpt-setup" className="sr-only">{query.data.chatgpt.instruction}</span>
                  </>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function Registration({ agent }: { agent: Agent }) {
  const at = agent.registered_at && Number.isFinite(Date.parse(agent.registered_at)) ? agent.registered_at : null
  if (!agent.registered_by) return at ? <time dateTime={at}>{shortDate(at)}</time> : null
  return <span className="flex min-w-0 items-center gap-2">
    {agent.registered_by !== "you" && <Logo kind={agent.registered_by} className="size-5" />}
    <span className="truncate">{agent.registered_by}{at ? `, ${shortDate(at)}` : ""}</span>
  </span>
}

function Claim({ agent }: { agent: Agent }) {
  const href = agent.claim_url && /^\/routines\/claim\/owner\/[a-f0-9]{32}$/.test(agent.claim_url) ? agent.claim_url : null
  return href ? <Button className="font-sans" size="sm" variant="outline" asChild><a href={href}>Claim</a></Button>
    : <span className="text-body-sm text-muted-foreground">Awaiting handoff</span>
}

function AgentList({ agents, state }: { agents: Agent[]; state?: DataStateProps }) {
  return (
    <section aria-labelledby="registered-agents" className="mt-8">
      <div className="flex items-center justify-between gap-3">
        <h2 id="registered-agents" className="text-base leading-6 font-semibold tracking-[-0.01em]">Registered</h2>
        {!state && <span className="text-body-sm text-muted-foreground">{agents.length} {agents.length === 1 ? "agent" : "agents"}</span>}
      </div>
      {state ? <DataState {...state} /> : !agents.length ? <DataState kind="empty" message="No registered agents." /> : (
        <>
          <table className="hidden w-full table-fixed text-left text-[13px] leading-[18px] md:table">
            <thead className="border-b text-[11px] leading-4 font-medium text-muted-foreground">
              <tr>
                <th scope="col" className="w-[18.3%] py-2 font-medium">Agent</th>
                <th scope="col" className="w-[11.3%] py-2 font-medium">Kind</th>
                <th scope="col" className="w-[16.7%] py-2 font-medium">Projects</th>
                <th scope="col" className="py-2 font-medium">Registered by</th>
                <th scope="col" className="w-[10%] py-2 font-medium">Last seen</th>
                <th scope="col" className="w-8"><span className="sr-only">Status</span></th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.id} className="relative h-[45px] border-b hover:bg-muted/50 focus-within:bg-muted/50">
                  <td className="pr-2">
                    {agent.type === "pending" ? <span className="flex items-center gap-2 font-semibold"><Logo kind={agent.logo} />{agent.name}</span> : <Link to={detailPath(agent.id)} className="flex items-center gap-2 rounded-sm font-semibold before:absolute before:inset-0">
                      <Logo kind={agent.logo} className="size-[22px]" /><span className="truncate">{agent.name}</span>
                    </Link>}
                  </td>
                  <td className="pr-2 text-muted-foreground">{agent.kind}</td>
                  <td className="truncate pr-2 font-mono text-xs" title={agent.projects.join(", ") || undefined}>{agent.projects.join(", ") || <span className="text-muted-foreground">none</span>}</td>
                  <td className="truncate pr-2"><Registration agent={agent} /></td>
                  <td className="relative">{agent.type === "pending" ? <Claim agent={agent} />
                    : <span className="font-mono text-xs text-muted-foreground">{elapsed(agent.last_seen)}</span>}</td>
                  <td className="text-right"><AgentLamp state={agent.state} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="md:hidden">
            {agents.map((agent) => {
              const heading = <><Logo kind={agent.logo} /><span className="min-w-0">
                <span className="block truncate text-[13px] font-semibold">{agent.name}</span>
                <span className="block truncate text-xs text-muted-foreground">{agent.kind}{agent.projects.length ? ` / ${agent.projects.join(", ")}` : ""}</span>
              </span></>
              return (
                <li key={agent.id} className="relative flex items-center justify-between gap-3 border-b py-3">
                  {agent.type === "pending" ? <span className="flex min-w-0 items-center gap-3">{heading}</span>
                    : <Link to={detailPath(agent.id)} className="flex min-w-0 items-center gap-3 rounded-sm before:absolute before:inset-0">{heading}</Link>}
                  <span className="flex shrink-0 items-center gap-2 text-xs">
                    {agent.type === "pending" ? <span className="relative"><Claim agent={agent} /></span> : <>
                      {agent.last_seen && <span className="font-mono text-muted-foreground">{elapsed(agent.last_seen)}</span>}
                      <AgentLamp state={agent.state} />
                    </>}
                  </span>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}

function Overview({ agent }: { agent: Agent }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-3 py-4 text-[13px] leading-[18px]">
      <dt className="text-muted-foreground">Kind</dt><dd>{agent.kind}</dd>
      <dt className="text-muted-foreground">Projects</dt><dd className="font-mono text-xs">{agent.projects.join(", ") || "none"}</dd>
      <dt className="text-muted-foreground">Registered by</dt><dd><Registration agent={agent} /></dd>
      <dt className="text-muted-foreground">Last seen</dt><dd className="flex items-center gap-2 font-mono text-xs"><AgentLamp state={agent.state} />{elapsed(agent.last_seen)}</dd>
    </dl>
  )
}

function AgentPanel({ agent, detail }: { agent: Agent; detail: AgentDetail }) {
  const tabs = ["Overview", "Routines", "Keys", "Activity"] as const
  const [confirmKey, setConfirmKey] = useState<string>()
  const session = useQuery(sessionQuery)
  const client = useQueryClient()
  const navigate = useNavigate()
  const revoke = useMutation({
    mutationFn: (id: string) => requestJson(`/api/pairing/keys/${encodeURIComponent(id)}/revoke`, (data): data is { revoked: true } =>
      !!data && typeof data === "object" && "revoked" in data && data.revoked === true, { method: "POST", csrf: session.data?.csrf }),
    onSuccess: (_, id) => {
      setConfirmKey(undefined)
      toast.success("Key revoked")
      if (agent.type === "key" && agent.reference === id) void navigate("/agents", { replace: true })
      void client.invalidateQueries({ queryKey: agentListKey })
      void client.invalidateQueries({ queryKey: ["agent-detail"] })
    },
    onError: () => toast.error("Couldn’t revoke key"),
  })

  return (
    <Tabs defaultValue="Overview">
      <TabsList aria-label="Agent detail" className="gap-0.5">
        {tabs.map((name) => <TabsTrigger key={name} value={name}>{name}</TabsTrigger>)}
      </TabsList>
      <TabsContent value="Overview"><Overview agent={agent} /></TabsContent>
      <TabsContent value="Routines">{detail.routines.length ? <ul className="text-[13px]">
        {detail.routines.map((routine) => <li key={`${routine.block}:${routine.name}`} className="flex items-center justify-between gap-2 border-b py-3">
          <span className="font-medium">{routine.name}</span>
          <span className="text-muted-foreground">{routine.project || "general"}{routine.primary ? " (primary)" : ""}</span>
        </li>)}
      </ul> : <DataState kind="empty" message="No routines." />}</TabsContent>
      <TabsContent value="Keys">{detail.keys.length ? <ul className="text-[13px]">
        {detail.keys.map((key) => <li key={key.id} className="flex flex-wrap items-center justify-between gap-2 border-b py-3">
          <div>
            <p className="font-mono text-xs">{key.id}</p>
            <p className="text-muted-foreground">{key.project || "All projects"}{key.created_at ? `, added ${shortDate(key.created_at)}` : ""}</p>
          </div>
          {confirmKey === key.id ? <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setConfirmKey(undefined)} disabled={revoke.isPending}>Cancel</Button>
            <Button size="sm" variant="destructive" disabled={revoke.isPending || !session.data?.csrf} onClick={() => revoke.mutate(key.id)}>Revoke key</Button>
          </div> : <Button size="sm" variant="outline" onClick={() => setConfirmKey(key.id)} disabled={revoke.isPending}>Revoke</Button>}
        </li>)}
      </ul> : <DataState kind="empty" message="No revocable keys." />}</TabsContent>
      <TabsContent value="Activity">{detail.audit.length ? <ul className="text-[13px]">
        {detail.audit.map((entry) => <li key={entry.id} className="flex items-center justify-between gap-3 border-b py-3">
          <span>{auditText(entry)}</span>
          <span className="shrink-0 font-mono text-xs text-muted-foreground">{shortDate(entry.at)}</span>
        </li>)}
      </ul> : <DataState kind="empty" message="No audit activity." />}</TabsContent>
    </Tabs>
  )
}

function AgentInspector({ agent }: { agent: Agent }) {
  const navigate = useNavigate()
  const query = useQuery({
    queryKey: ["agent-detail", agent.id],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ kind: agent.type, id: agent.reference })
      if (agent.block) params.set("block", agent.block)
      return requestJson(`/api/agents/detail?${params}`, isAgentDetail, { signal })
    },
  })
  const panel = query.isPending ? <DataState kind="loading" message="Loading agent…" /> : query.isError ? (
    <DataState kind="error" message="Couldn’t load agent." onRetry={() => void query.refetch()} />
  ) : <AgentPanel key={agent.id} agent={agent} detail={query.data} />

  const [desktop, setDesktop] = useState(() => matchMedia("(min-width: 768px)").matches)
  useEffect(() => {
    const media = matchMedia("(min-width: 768px)")
    function update() { setDesktop(media.matches) }
    media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])
  return desktop ? <Sheet open title={agent.name} onOpenChange={(open) => { if (!open) void navigate("/agents", { replace: true }) }}>{panel}</Sheet> : (
    <section aria-label="Agent details">
      <Link to="/agents" className="inline-flex items-center gap-1.5 rounded-sm text-[13px] text-muted-foreground hover:text-foreground"><ArrowLeft aria-hidden className="size-4" strokeWidth={1.5} /> Registered agents</Link>
      <h2 className="my-4 flex items-center gap-2 text-base font-semibold"><Logo kind={agent.logo} />{agent.name}</h2>
      {panel}
    </section>
  )
}

export function AgentsPage() {
  const id = useParams()["*"]
  const query = useQuery({ queryKey: agentListKey, queryFn: ({ signal }) => listAgents(signal) })
  const agent = id ? query.data?.find((row) => row.id === id && row.type !== "pending") : undefined
  const state: DataStateProps | undefined = query.isPending ? { kind: "loading", message: "Loading agents…" } : query.isError ? {
    kind: "error", message: "Couldn’t load agents.", onRetry: () => void query.refetch(),
  } : undefined
  return (
    <>
      <div className={agent ? "hidden pt-2 md:block" : "pt-2"}>
        <ConnectionRow />
        <AgentList agents={query.data ?? []} state={state} />
      </div>
      {agent && <AgentInspector agent={agent} />}
      {id && query.data && !agent && <DataState kind="empty" message="Agent not found." />}
    </>
  )
}
