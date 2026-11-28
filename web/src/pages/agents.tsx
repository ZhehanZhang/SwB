import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft } from "lucide-react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { toast } from "sonner"
import { ConfirmAction } from "@/components/console/confirm-action"
import { AvatarTile } from "@/components/avatar-tile"
import { Time } from "@/components/time"
import { Lamp } from "@/components/console/lamp"
import { DataState, type DataStateProps } from "@/components/data-state"
import { LoadingSkeleton, Skeleton } from "@/components/loading-skeleton"
import { Logo } from "@/components/logo"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/tabs"
import { Button } from "@/components/ui/button"
import { Sheet } from "@/components/ui/sheet"
import { principalLabel } from "@/lib/agent-identity"
import { isObject, requestJson, sessionQuery } from "@/lib/api"
import { AgentSetup } from "./agents/setup"
import { auditText } from "./audit/events"
import { agentDetailKey, agentListKey, getAgentDetail, listAgents, type Agent, type AgentDetail } from "./agents/data"

const detailPath = (id: string) => `/agents/${encodeURIComponent(id)}`
function AgentLamp({ state }: { state: Agent["state"] }) {
  return <Lamp state={state} label={state === "on" ? "Active" : state === "fault" ? "Needs attention" : "Inactive"} />
}

function Registration({ agent }: { agent: Agent }) {
  const at = agent.registered_at && Number.isFinite(Date.parse(agent.registered_at)) ? agent.registered_at : null
  if (!agent.registered_by) return <Time value={at} />
  return <span className="flex min-w-0 items-center gap-2">
    {agent.registered_by !== "you" && <Logo kind={agent.registered_by} className="size-5" />}
    <span className="truncate">{principalLabel(agent.registered_by)}{at && <>, <Time value={at} /></>}</span>
  </span>
}

function Claim({ agent }: { agent: Agent }) {
  const href = agent.claim_url && /^\/routines\/claim\/owner\/[a-f0-9]{32}$/.test(agent.claim_url) ? agent.claim_url : null
  return href ? <Button className="font-sans" size="sm" variant="outline" asChild><a href={href}>Claim</a></Button>
    : <span className="text-body-sm text-muted-foreground">Claim pending</span>
}

function AgentListSkeleton() {
  return (
    <LoadingSkeleton message="Loading agents…">
      <div className="hidden h-row items-center gap-5 border-b md:flex">
        <Skeleton className="h-2.5 w-3/20" />
        <Skeleton className="h-2.5 w-1/10" />
        <Skeleton className="h-2.5 w-3/20" />
      </div>
      {[0, 1].map((row) => (
        <div key={row} className="flex h-16 items-center gap-3 border-b md:h-row">
          <Skeleton className="size-6 rounded-md md:size-5" />
          <div className="space-y-1.5 md:w-3/20">
            <Skeleton className="h-3 w-24 max-w-full" />
            <Skeleton className="h-2.5 w-16 md:hidden" />
          </div>
          <Skeleton className="hidden h-3 w-1/10 md:block" />
          <Skeleton className="hidden h-3 w-7/50 md:block" />
          <Skeleton className="hidden h-3 w-9/50 md:block" />
          <Skeleton className="ml-auto h-2.5 w-8" />
          <Skeleton className="size-2 rounded-full" />
        </div>
      ))}
    </LoadingSkeleton>
  )
}

function AgentList({ agents, state }: { agents: Agent[]; state?: DataStateProps }) {
  return (
    <section aria-labelledby="registered-agents" className="mt-8">
      <div className="flex items-center justify-between gap-3">
        <h2 id="registered-agents" className="text-heading-md leading-6 font-semibold">Registered</h2>
        {state?.kind === "loading" ? <Skeleton className="h-3 w-12" /> : !state && agents.length > 0 && (
          <span className="text-body-sm text-muted-foreground">{agents.length} {agents.length === 1 ? "agent" : "agents"}</span>
        )}
      </div>
      {state?.kind === "loading" ? <AgentListSkeleton /> : state ? <DataState {...state} /> : !agents.length ? <DataState kind="empty" message="0 agents" /> : (
        <>
          <table className="hidden w-full table-fixed text-left text-body-sm md:table">
            <thead className="border-b text-body-sm font-medium text-muted-foreground">
              <tr>
                <th scope="col" className="w-183/1000 py-2 font-medium">Agent</th>
                <th scope="col" className="w-113/1000 py-2 font-medium">Kind</th>
                <th scope="col" className="w-167/1000 py-2 font-medium">Projects</th>
                <th scope="col" className="py-2 font-medium">Registered by</th>
                <th scope="col" className="w-1/10 py-2 font-medium">Last seen</th>
                <th scope="col" className="w-8"><span className="sr-only">Status</span></th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr key={agent.id} className="relative h-row border-b hover:bg-muted/50 focus-within:bg-muted/50">
                  <td className="pr-2">
                    {agent.type === "pending" ? <span className="flex items-center gap-2 font-semibold"><AvatarTile kind={agent.logo} />{agent.name}</span> : <Link to={detailPath(agent.id)} className="flex items-center gap-2 rounded-sm font-semibold before:absolute before:inset-0">
                      <AvatarTile kind={agent.logo} avatarUrl={agent.avatar_url} /><span className="truncate">{agent.name}</span>
                    </Link>}
                  </td>
                  <td className="pr-2 text-muted-foreground">{agent.kind}</td>
                  <td className="truncate pr-2 text-body-sm" title={agent.projects.join(", ") || undefined}>{agent.projects.join(", ")}</td>
                  <td className="truncate pr-2"><Registration agent={agent} /></td>
                  <td className="relative">{agent.type === "pending" ? <Claim agent={agent} />
                    : <Time value={agent.last_seen} />}</td>
                  <td className="text-right"><AgentLamp state={agent.state} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="md:hidden">
            {agents.map((agent) => {
              const heading = <><AvatarTile kind={agent.logo} avatarUrl={agent.avatar_url} /><span className="min-w-0">
                <span className="block truncate text-body-sm font-semibold">{agent.name}</span>
                <span className="block truncate text-label-sm text-muted-foreground">{agent.kind}{agent.projects.length ? ` / ${agent.projects.join(", ")}` : ""}</span>
              </span></>
              return (
                <li key={agent.id} className="relative flex items-center justify-between gap-3 border-b py-3">
                  {agent.type === "pending" ? <span className="flex min-w-0 items-center gap-3">{heading}</span>
                    : <Link to={detailPath(agent.id)} className="flex min-w-0 items-center gap-3 rounded-sm before:absolute before:inset-0">{heading}</Link>}
                  <span className="flex shrink-0 items-center gap-2 text-label-sm">
                    {agent.type === "pending" ? <span className="relative"><Claim agent={agent} /></span> : <>
                      <Time value={agent.last_seen} />
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
    <dl className="grid grid-cols-auto-main gap-x-5 gap-y-3 py-4 text-body-sm">
      <dt className="text-muted-foreground">Kind</dt><dd>{agent.kind}</dd>
      <dt className="text-muted-foreground">Projects</dt><dd>{agent.projects.join(", ")}</dd>
      <dt className="text-muted-foreground">Registered by</dt><dd><Registration agent={agent} /></dd>
      <dt className="text-muted-foreground">Last seen</dt><dd className="flex items-center gap-2"><AgentLamp state={agent.state} /><Time value={agent.last_seen} /></dd>
    </dl>
  )
}

function AgentPanel({ agent, detail }: { agent: Agent; detail: AgentDetail }) {
  const tabs = ["Overview", "Routines", "Tokens", "Activity"] as const
  const [confirmKey, setConfirmKey] = useState<string>()
  const session = useQuery(sessionQuery)
  const client = useQueryClient()
  const navigate = useNavigate()
  const revoke = useMutation({
    mutationFn: (id: string) => requestJson(`/api/pairing/keys/${encodeURIComponent(id)}/revoke`, (data): data is { revoked: true } =>
      !!data && typeof data === "object" && "revoked" in data && data.revoked === true, { method: "POST", csrf: session.data?.csrf }),
    onSuccess: (_, id) => {
      setConfirmKey(undefined)
      toast.success("Token revoked")
      if (agent.type === "key" && agent.reference === id) void navigate("/agents", { replace: true })
      void client.invalidateQueries({ queryKey: agentListKey })
      void client.invalidateQueries({ queryKey: agentDetailKey })
    },
    onError: () => toast.error("Token revocation failed"),
  })
  const removeRoutine = useMutation({
    mutationFn: ({ name, block }: { name: string; block: string }) =>
      requestJson(`/api/routines/${encodeURIComponent(name)}/remove?${new URLSearchParams({ block })}`,
        (value): value is { removed: true } => isObject(value) && value.removed === true,
        { method: "POST", csrf: session.data?.csrf }),
    onSuccess: (_, routine) => {
      if (agent.type === "routine" && agent.reference === routine.name && agent.block === routine.block) {
        void navigate("/agents", { replace: true })
      }
      void client.invalidateQueries({ queryKey: agentListKey })
      void client.invalidateQueries({ queryKey: agentDetailKey })
      void client.invalidateQueries({ queryKey: ["tokens"] })
    },
  })

  return (
    <Tabs defaultValue="Overview">
      <TabsList aria-label="Agent details" className="gap-0.5">
        {tabs.map((name) => <TabsTrigger key={name} value={name}>{name}</TabsTrigger>)}
      </TabsList>
      <TabsContent value="Overview"><Overview agent={agent} /></TabsContent>
      <TabsContent value="Routines">
        {detail.routines.length ? <ul className="text-body-sm">
          {detail.routines.map((routine) => <li key={`${routine.block}:${routine.name}`} className="flex flex-wrap items-center gap-2 border-b py-3">
            <span className="me-auto font-medium">{routine.name}</span>
            <span className="text-muted-foreground">{routine.project}{routine.primary && (routine.project ? ", primary" : "Primary")}</span>
            {routine.source === "stored" && routine.agent !== "grok" && (
              <ConfirmAction title={`Remove ${routine.name}?`} action="Remove" onConfirm={() => removeRoutine.mutate(routine)}>
                <Button variant="ghost" size="sm" aria-label={`Remove routine ${routine.name}`}
                  disabled={!session.data?.csrf || removeRoutine.isPending}>Remove</Button>
              </ConfirmAction>
            )}
          </li>)}
        </ul> : <DataState kind="empty" message="0 routines" />}
        {removeRoutine.isError && <p role="alert" className="mt-2 text-body-sm text-destructive">Try removing the routine again.</p>}
      </TabsContent>
      <TabsContent value="Tokens">{detail.keys.length ? <ul className="text-body-sm">
        {detail.keys.map((key) => <li key={key.id} className="flex flex-wrap items-center justify-between gap-2 border-b py-3">
          <div>
            <p className="font-mono text-mono-sm">{key.id}</p>
            <p className="text-muted-foreground">{key.project || "All projects"}{key.created_at && <>, added <Time value={key.created_at} /></>}</p>
          </div>
          {confirmKey === key.id ? <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={() => setConfirmKey(undefined)} disabled={revoke.isPending}>Cancel</Button>
            <Button size="sm" variant="destructive" disabled={revoke.isPending || !session.data?.csrf} onClick={() => revoke.mutate(key.id)}>Revoke token</Button>
          </div> : <Button size="sm" variant="outline" onClick={() => setConfirmKey(key.id)} disabled={revoke.isPending}>Revoke</Button>}
        </li>)}
      </ul> : <DataState kind="empty" message="0 tokens to revoke" />}</TabsContent>
      <TabsContent value="Activity">{detail.audit.length ? <ul className="text-body-sm">
        {detail.audit.map((entry) => <li key={entry.id} className="flex items-center justify-between gap-3 border-b py-3">
          <span>{auditText(entry)}</span>
          <Time value={entry.at} className="shrink-0" />
        </li>)}
      </ul> : <DataState kind="empty" message="0 events" />}</TabsContent>
    </Tabs>
  )
}

function AgentBackLink() {
  return (
    <Link to="/agents" className="inline-flex items-center gap-1.5 rounded-sm text-body-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft aria-hidden className="size-4" strokeWidth={1.5} /> Registered agents
    </Link>
  )
}

function AgentDetailSkeleton({ loadingName = false }: { loadingName?: boolean }) {
  return (
    <LoadingSkeleton message="Loading agent…">
      {loadingName && (
        <div className="my-4 flex h-6 items-center gap-2">
          <Skeleton className="size-6 rounded-md" />
          <Skeleton className="h-4 w-24" />
        </div>
      )}
      <div className="flex h-9 items-center gap-3 border-b sm:gap-5">
        {[0, 1, 2, 3].map((tab) => <Skeleton key={tab} className="h-3 w-10 first:ml-2" />)}
      </div>
      <div className="space-y-3 py-4">
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className="flex h-4.5 items-center gap-5">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-3 w-24" />
          </div>
        ))}
      </div>
    </LoadingSkeleton>
  )
}

function AgentInspector({ detail }: { detail: AgentDetail }) {
  const { agent } = detail
  const navigate = useNavigate()
  const panel = <AgentPanel key={agent.id} agent={agent} detail={detail} />

  const [desktop, setDesktop] = useState(() => matchMedia("(min-width: 48rem)").matches)
  useEffect(() => {
    const media = matchMedia("(min-width: 48rem)")
    function update() { setDesktop(media.matches) }
    media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])
  return desktop ? <Sheet open title={agent.name} onOpenChange={(open) => { if (!open) void navigate("/agents", { replace: true }) }}>{panel}</Sheet> : (
    <section aria-label="Agent details">
      <AgentBackLink />
      <h2 className="my-4 flex items-center gap-2 text-heading-md font-semibold"><AvatarTile kind={agent.logo} avatarUrl={agent.avatar_url} />{agent.name}</h2>
      {panel}
    </section>
  )
}

export function AgentsPage() {
  const id = useParams()["*"] ?? ""
  const query = useQuery({ queryKey: agentListKey, queryFn: ({ signal }) => listAgents(signal) })
  const registeredPath = id.includes(":")
  const registered = registeredPath ? query.data?.find((row) => row.id === id && row.type !== "pending") : undefined
  const detail = useQuery({
    queryKey: [...agentDetailKey, id],
    queryFn: ({ signal }) => getAgentDetail(registered ?? id, signal),
    enabled: Boolean(id) && (!registeredPath || !query.isPending),
  })
  const state: DataStateProps | undefined = query.isPending ? { kind: "loading" } : query.isError ? {
    kind: "error", message: "Agents unavailable.", onRetry: () => void query.refetch(),
  } : undefined
  return (
    <>
      <div className={detail.data || (id && detail.isPending) ? "hidden pt-2 md:block" : "pt-2"}>
        <AgentSetup agents={query.data ?? []} />
        <AgentList agents={query.data ?? []} state={state} />
      </div>
      {id && detail.isPending && (
        <section aria-label="Agent details">
          <AgentBackLink />
          <AgentDetailSkeleton loadingName />
        </section>
      )}
      {detail.data && <AgentInspector detail={detail.data} />}
      {id && detail.isError && <DataState kind="error" message="Agent unavailable." onRetry={() => void detail.refetch()} />}
      {id && detail.data === null && <DataState kind="empty" message="Unknown agent." />}
    </>
  )
}
