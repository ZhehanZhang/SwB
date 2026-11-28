import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { X } from "lucide-react"
import { toast } from "sonner"
import { Lamp } from "@/components/console/lamp"
import { ConfirmAction } from "@/components/console/confirm-action"
import { DataState } from "@/components/data-state"
import { Disclosure } from "@/components/disclosure"
import { LoadingSkeleton, Skeleton } from "@/components/loading-skeleton"
import { LogoTile } from "@/components/logo-tile"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { isObject, requestJson, sessionQuery } from "@/lib/api"
import { connectorKind } from "@/lib/connector-kind"
import { cn } from "@/lib/utils"

type Credential = {
  id: string
  name: string
  label: string
  kind: string
  used_by: string[]
}

type Waiting = Credential & {
  asked_by: string | null
  requested_at: string | null
  expires_at: string | null
}

type Stored = Credential & {
  added_by: string | null
  added_at: string | null
  revoke_id: string | null
  routine_block?: string
  memory_scopes?: string[]
}

type Tokens = { waiting: Waiting[]; stored: Stored[] }

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === "string"
}

function timestamp(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && Number.isFinite(Date.parse(value)))
}

function isCredential(value: unknown): value is Credential & Record<string, unknown> {
  return isObject(value) && typeof value.id === "string" && typeof value.name === "string"
    && typeof value.label === "string" && typeof value.kind === "string"
    && Array.isArray(value.used_by) && value.used_by.every((entry) => typeof entry === "string")
}

function isTokens(value: unknown): value is Tokens {
  return isObject(value) && Array.isArray(value.waiting) && Array.isArray(value.stored)
    && value.waiting.every((row) => isCredential(row) && nullableText(row.asked_by)
      && timestamp(row.requested_at) && timestamp(row.expires_at))
    && value.stored.every((row) => isCredential(row) && nullableText(row.added_by)
      && timestamp(row.added_at) && (row.revoke_id === null || typeof row.revoke_id === "string")
      && (row.routine_block === undefined || typeof row.routine_block === "string")
      && (row.memory_scopes === undefined || (Array.isArray(row.memory_scopes) && row.memory_scopes.every((scope) => typeof scope === "string"))))
}

const queryKey = ["tokens"] as const

function memoryScopeLabel(scope: string) {
  if (scope === "general") return "General"
  if (scope.startsWith("agent:")) return `Agent ${scope.slice(6)}`
  return scope.replace(/^project:/, "")
}

function Identity({ row }: { row: Stored }) {
  return (
    <span className="flex items-center gap-2">
      <LogoTile kind={row.revoke_id ? connectorKind(row.label) ?? row.kind : row.kind} />
      <span className="min-w-0 wrap-anywhere">{row.name}, {row.label}</span>
    </span>
  )
}

function RevokeKey({ label, disabled, pending, onRevoke }: {
  label: string
  disabled: boolean
  pending: boolean
  onRevoke: () => void
}) {
  return (
    <ConfirmAction title="Revoke access key?" subject={label} action="Revoke" onConfirm={onRevoke}>
        <Button size="sm" variant="ghost" className="max-md:min-h-8 max-md:pr-0" disabled={disabled} aria-label={`Revoke access key for ${label}`}>
          {pending ? "Revoking…" : "Revoke"}
        </Button>
    </ConfirmAction>
  )
}

export function TokensPage() {
  const client = useQueryClient()
  const { data: session } = useQuery(sessionQuery)
  const tokens = useQuery({
    queryKey,
    queryFn: ({ signal }) => requestJson("/api/tokens", isTokens, { signal }),
    refetchInterval: 30_000,
  })
  const revoke = useMutation({
    mutationFn: (id: string) => requestJson(
      `/api/pairing/keys/${encodeURIComponent(id)}/revoke`,
      (value): value is { revoked: true } => isObject(value) && value.revoked === true,
      { method: "POST", csrf: session?.csrf },
    ),
    onSuccess: () => toast.success("Key revoked."),
    onError: () => toast.error("Try revoking the key again."),
    onSettled: () => client.invalidateQueries({ queryKey }),
  })
  const removeScope = useMutation({
    mutationFn: ({ id, scope }: { id: string; scope: string }) => requestJson(
      `/api/pairing/keys/${encodeURIComponent(id)}/memory/remove`,
      (data): data is { removed: boolean } => isObject(data) && typeof data.removed === "boolean",
      { method: "POST", csrf: session?.csrf, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope }) },
    ),
    onSuccess: () => client.invalidateQueries({ queryKey }),
    onError: () => toast.error("Try removing the scope again."),
  })
  const removeRoutine = useMutation({
    mutationFn: ({ label, routine_block }: Stored) => requestJson(
      `/api/routines/${encodeURIComponent(label)}/remove?${new URLSearchParams({ block: routine_block ?? "" })}`,
      (data): data is { removed: true } => isObject(data) && data.removed === true,
      { method: "POST", csrf: session?.csrf },
    ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey })
      void client.invalidateQueries({ queryKey: ["registered-agents"] })
    },
  })

  if (!tokens.isPending && !tokens.data) return <DataState kind="error" message="Tokens unavailable." onRetry={() => void tokens.refetch()} />

  const { waiting = [], stored = [] } = tokens.data ?? {}

  return (
    <div className="space-y-8 pt-2">
      <section aria-labelledby="waiting-tokens">
        <h2 id="waiting-tokens" className="text-heading-md">Token requests</h2>
        {tokens.isPending ? (
          <div aria-hidden="true" className="mt-2 flex h-16 items-center gap-3 border-b">
            <Skeleton className="size-7 rounded-md" />
            <div className="min-w-0 space-y-1.5">
              <Skeleton className="h-3 w-28 max-w-full" />
              <Skeleton className="h-2.5 w-20 max-w-full" />
            </div>
            <Skeleton className="ml-auto h-2.5 w-16" />
          </div>
        ) : waiting.length === 0
          ? <DataState kind="empty" message="0 requests" />
          : <ul className="mt-2">
              {waiting.map((row) => {
                const detail = [
                  row.asked_by && `Asked by ${row.asked_by}`,
                  row.used_by.length > 0 && `${row.asked_by ? "for" : "For"} ${row.used_by.join(", ")}`,
                ].filter(Boolean).join(" ")
                return (
                  <li key={row.id} className="grid min-h-16 gap-x-4 gap-y-1.5 border-b py-3 md:grid-cols-fill-auto md:items-center">
                    <div className="flex min-w-0 items-center gap-2">
                      <LogoTile kind={row.kind} className="size-7" />
                      <div className="min-w-0">
                        <p className="text-body-sm font-semibold wrap-anywhere">{row.name}, {row.label}</p>
                        {(detail || row.requested_at) && <p className="text-body-sm leading-5 text-muted-foreground wrap-anywhere">
                          {detail}{detail && row.requested_at ? ", " : ""}<Time value={row.requested_at} />
                        </p>}
                      </div>
                    </div>
                    <span className="ml-9 flex items-center gap-2 text-label-sm text-muted-foreground md:ml-0" title={row.expires_at
                      ? `Expires ${new Date(row.expires_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}`
                      : undefined}>
                      <Lamp state="off" /> Open the request link
                    </span>
                  </li>
                )
              })}
            </ul>}
      </section>
      <section aria-labelledby="stored-tokens">
        <h2 id="stored-tokens" className="text-heading-md">Stored</h2>
        {tokens.isPending ? (
          <LoadingSkeleton message="Loading tokens…" className="mt-2">
            <div className="hidden h-7 items-start gap-5 border-b md:flex">
              {[0, 1, 2, 3, 4, 5].map((column) => <Skeleton key={column} className="h-2.5 w-3/25" />)}
            </div>
            {[0, 1].map((row) => (
              <div key={row} className="flex h-18 flex-col justify-center gap-2 border-b md:h-12 md:flex-row md:items-center md:justify-start md:gap-4">
                <div className="flex items-center gap-2 md:w-9/50">
                  <Skeleton className="size-6 rounded-md" />
                  <Skeleton className="h-3 w-24 max-w-1/2" />
                  <Skeleton className="ml-auto h-2.5 w-7 md:hidden" />
                </div>
                <Skeleton className="hidden h-2.5 w-3/25 md:block" />
                <Skeleton className="h-3 w-24 md:w-1/5" />
                <Skeleton className="hidden h-3 w-3/25 md:block" />
                <Skeleton className="hidden h-2.5 w-2/25 md:block" />
              </div>
            ))}
          </LoadingSkeleton>
        ) : stored.length === 0
          ? <DataState kind="empty" message="0 stored tokens" />
          : <table className="mt-2 w-full text-left text-body-sm md:table-fixed" aria-labelledby="stored-tokens" role="table">
              <thead className="sr-only md:not-sr-only md:table-header-group" role="rowgroup">
                <tr className="border-b text-body-sm text-muted-foreground" role="row">
                  <th scope="col" className="w-7/25 pb-2 font-medium">Name</th>
                  <th scope="col" className="w-14 pb-2 font-medium">Value</th>
                  <th scope="col" className="pb-2 font-medium">Used by</th>
                  <th scope="col" className="w-11/50 pb-2 font-medium">Memory access</th>
                  <th scope="col" className="w-3/25 pb-2 font-medium">Added by</th>
                  <th scope="col" className="w-1/10 pb-2 font-medium">Added</th>
                  <th scope="col" className="w-20"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="block md:table-row-group" role="rowgroup">
                {stored.map((row) => {
                  const id = row.revoke_id
                  return (
                    <tr key={row.id} className="grid grid-cols-fill-auto items-center gap-x-4 gap-y-1 border-b py-3 md:table-row md:h-12" role="row">
                      <th scope="row" className="min-w-0 font-normal md:py-2 md:pr-4"><Identity row={row} /></th>
                      <td className="justify-self-end text-mono-sm text-muted-foreground md:pr-4" role="cell">
                        <span aria-hidden>••••</span><span className="sr-only">Hidden</span>
                      </td>
                      <td className={cn("col-span-2 wrap-anywhere text-muted-foreground max-md:pl-8 md:pr-4 md:text-foreground", !row.used_by.length && "max-md:hidden")} role="cell">
                        {row.used_by.length > 0 && <><span className="md:sr-only">Used by </span>{row.used_by.join(", ")}</>}
                      </td>
                      <td className={cn("col-span-2 max-md:pl-8 md:pr-4", !row.memory_scopes && "max-md:hidden")} role="cell">
                        {row.memory_scopes && (
                          <div className="grid justify-items-start">
                            <span className="text-muted-foreground md:sr-only">Memory access</span>
                            {row.memory_scopes.length === 0 && <span className="text-muted-foreground">0 scopes</span>}
                            {row.memory_scopes.map((scope) => {
                              const label = memoryScopeLabel(scope)
                              return (
                                <div key={scope} className="inline-flex max-w-full items-center gap-1">
                                  <span className="min-w-0 wrap-anywhere">{label}</span>
                                  {id && <ConfirmAction title={`Remove ${label} access?`} subject={row.label} action="Remove"
                                    onConfirm={() => removeScope.mutate({ id, scope })}>
                                    <Button size="icon" variant="ghost" className="size-8 shrink-0"
                                      aria-label={`Remove ${label} from ${row.label}`} title="Remove scope"
                                      disabled={!session?.csrf || removeScope.isPending}><X aria-hidden strokeWidth={1.5} /></Button>
                                  </ConfirmAction>}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </td>
                      <td className={cn("col-span-2 wrap-anywhere text-muted-foreground max-md:pl-8 md:pr-4 md:text-foreground", !row.added_by && "max-md:hidden")} role="cell">
                        {row.added_by && <><span className="md:sr-only">Added by </span>{row.added_by}</>}
                      </td>
                      <td className={cn("text-body-sm text-muted-foreground max-md:pl-8 md:pr-2", !row.added_at && "max-md:hidden")} role="cell">
                        <Time value={row.added_at} />
                      </td>
                      <td className={cn("col-start-2 justify-self-end text-right", !row.revoke_id && !row.routine_block && "max-md:hidden")} role="cell">
                        {id && <RevokeKey
                          label={row.label}
                          disabled={!session?.csrf || revoke.isPending}
                          pending={revoke.isPending && revoke.variables === id}
                          onRevoke={() => revoke.mutate(id)}
                        />}
                        {row.routine_block && (
                          <ConfirmAction title="Remove routine?" subject={row.label} action="Remove" onConfirm={() => removeRoutine.mutate(row)}>
                            <Button size="sm" variant="ghost" disabled={!session?.csrf || removeRoutine.isPending}
                              aria-label={`Remove routine ${row.label}`}>Remove</Button>
                          </ConfirmAction>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>}
        {removeRoutine.isError && <p role="alert" className="mt-2 text-body-sm text-destructive">Try removing the routine again.</p>}
      </section>
      {session?.csrf && (
        <Disclosure summary="Pairing code" className="space-y-3">
          <form method="post" action="/pairing/code" className="flex flex-wrap items-end gap-3">
            <input type="hidden" name="csrf" value={session.csrf} />
            <label className="grid gap-1 text-body-sm">Agent name
              <input name="agent" required pattern="[a-z0-9][a-z0-9\-]{0,39}" className="rounded-md border bg-surface px-2 py-1" />
            </label>
            <label className="grid gap-1 text-body-sm">Project
              <input name="project" className="rounded-md border bg-surface px-2 py-1" />
            </label>
            <Button type="submit">Create code</Button>
          </form>
        </Disclosure>
      )}
    </div>
  )
}
