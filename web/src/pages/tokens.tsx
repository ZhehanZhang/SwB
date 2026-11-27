import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertDialog } from "radix-ui"
import { toast } from "sonner"
import { Lamp } from "@/components/console/lamp"
import { DataState } from "@/components/data-state"
import { LoadingSkeleton, Skeleton } from "@/components/loading-skeleton"
import { LogoTile } from "@/components/logo-tile"
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
      && timestamp(row.added_at) && (row.revoke_id === null || typeof row.revoke_id === "string"))
}

const queryKey = ["tokens"] as const
const date = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" })

function relative(at: string | null) {
  if (!at) return null
  const minutes = Math.floor((Date.now() - Date.parse(at)) / 60_000)
  if (!Number.isFinite(minutes)) return null
  if (minutes < 1) return "Just now"
  return minutes < 60 ? `${minutes} min ago` : `${Math.floor(minutes / 60)} h ago`
}

function Identity({ row }: { row: Stored }) {
  const kind = row.revoke_id ? connectorKind(row.label) ?? row.kind : row.kind
  return (
    <span className="flex items-center gap-2">
      <LogoTile kind={kind} />
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
    <AlertDialog.Root>
      <AlertDialog.Trigger asChild>
        <Button size="sm" variant="ghost" disabled={disabled} aria-label={`Revoke access key for ${label}`}>
          {pending ? "Revoking…" : "Revoke"}
        </Button>
      </AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-40 bg-overlay/50" />
        <AlertDialog.Content className="fixed top-1/2 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-1/2 rounded-lg border bg-card p-5 text-card-foreground shadow-lg">
          <AlertDialog.Title className="text-heading-md">Revoke access key?</AlertDialog.Title>
          <AlertDialog.Description className="mt-1 wrap-anywhere text-sm text-muted-foreground">{label}</AlertDialog.Description>
          <div className="mt-5 flex justify-end gap-2">
            <AlertDialog.Cancel asChild><Button variant="outline">Cancel</Button></AlertDialog.Cancel>
            <AlertDialog.Action asChild><Button variant="destructive" onClick={onRevoke}>Revoke</Button></AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
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

  if (!tokens.isPending && !tokens.data) return <DataState kind="error" message="Tokens unavailable." onRetry={() => void tokens.refetch()} />

  const { waiting = [], stored = [] } = tokens.data ?? {}

  return (
    <div className="space-y-8 pt-2">
      <section aria-labelledby="waiting-tokens">
        <h2 id="waiting-tokens" className="text-heading-md">Token requests</h2>
        {tokens.isPending ? (
          <div aria-hidden="true" className="mt-2 flex h-[67px] items-center gap-3 border-b">
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
                const age = relative(row.requested_at)
                return (
                  <li key={row.id} className="flex min-h-[67px] flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b py-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <LogoTile kind={row.kind} className="size-7" />
                      <div className="min-w-0">
                        <p className="text-[13px] leading-[18px] font-semibold wrap-anywhere">{row.name}, {row.label}</p>
                        {(detail || age) && <p className="text-[13px] leading-5 text-muted-foreground wrap-anywhere">
                          {detail || age}{detail && age ? `, ${age.toLowerCase()}` : ""}
                        </p>}
                      </div>
                    </div>
                    <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground" title={row.expires_at
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
            <div className="hidden h-[27px] items-start gap-5 border-b md:flex">
              {[0, 1, 2, 3, 4].map((column) => <Skeleton key={column} className="h-2.5 w-[12%]" />)}
            </div>
            {[0, 1].map((row) => (
              <div key={row} className="flex h-[71px] flex-col justify-center gap-2 border-b md:h-[49px] md:flex-row md:items-center md:justify-start md:gap-4">
                <div className="flex items-center gap-2 md:w-[18%]">
                  <Skeleton className="size-6 rounded-md" />
                  <Skeleton className="h-3 w-24 max-w-1/2" />
                  <Skeleton className="ml-auto h-2.5 w-7 md:hidden" />
                </div>
                <Skeleton className="hidden h-2.5 w-[12%] md:block" />
                <Skeleton className="h-3 w-24 md:w-[20%]" />
                <Skeleton className="hidden h-3 w-[12%] md:block" />
                <Skeleton className="hidden h-2.5 w-[8%] md:block" />
              </div>
            ))}
          </LoadingSkeleton>
        ) : stored.length === 0
          ? <DataState kind="empty" message="0 stored tokens" />
          : <table className="mt-2 w-full text-left text-[13px] leading-[18px]" aria-labelledby="stored-tokens" role="table">
              <thead className="sr-only md:not-sr-only md:table-header-group" role="rowgroup">
                <tr className="border-b text-[11px] text-muted-foreground" role="row">
                  <th scope="col" className="w-[19%] pb-2 font-medium">Name</th>
                  <th scope="col" className="w-[14%] pb-2 font-medium">Value</th>
                  <th scope="col" className="pb-2 font-medium">Used by</th>
                  <th scope="col" className="w-[14%] pb-2 font-medium">Added by</th>
                  <th scope="col" className="w-[11%] pb-2 font-medium">Added</th>
                  <th scope="col" className="w-16"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="block md:table-row-group" role="rowgroup">
                {stored.map((row) => {
                  const id = row.revoke_id
                  return (
                    <tr key={row.id} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 border-b py-3 md:table-row md:h-[49px]" role="row">
                      <th scope="row" className="min-w-0 font-normal md:py-2 md:pr-4"><Identity row={row} /></th>
                      <td className="font-mono text-xs text-muted-foreground md:pr-4" role="cell">
                        <span aria-hidden>••••</span><span className="sr-only">Hidden</span>
                      </td>
                      <td className={cn("col-span-2 wrap-anywhere text-muted-foreground md:pr-4 md:text-foreground", !row.used_by.length && "max-md:hidden")} role="cell">
                        {row.used_by.length > 0 && <><span className="md:sr-only">Used by </span>{row.used_by.join(", ")}</>}
                      </td>
                      <td className={cn("col-span-2 wrap-anywhere text-muted-foreground md:pr-4 md:text-foreground", !row.added_by && "max-md:hidden")} role="cell">
                        {row.added_by && <><span className="md:sr-only">Added by </span>{row.added_by}</>}
                      </td>
                      <td className={cn("font-mono text-xs text-muted-foreground md:pr-2", !row.added_at && "max-md:hidden")} role="cell">
                        {row.added_at && <time dateTime={row.added_at}>{date.format(new Date(row.added_at))}</time>}
                      </td>
                      <td className={cn("col-start-2 justify-self-end text-right", !row.revoke_id && "max-md:hidden")} role="cell">
                        {id && <RevokeKey
                          label={row.label}
                          disabled={!session?.csrf || revoke.isPending}
                          pending={revoke.isPending && revoke.variables === id}
                          onRevoke={() => revoke.mutate(id)}
                        />}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>}
      </section>
      {session?.csrf && (
        <details className="space-y-3">
          <summary className="w-fit cursor-pointer text-sm font-medium">Pairing code</summary>
          <form method="post" action="/pairing/code" className="flex flex-wrap items-end gap-3">
            <input type="hidden" name="csrf" value={session.csrf} />
            <label className="grid gap-1 text-sm">Agent name
              <input name="agent" required pattern="[a-z0-9][a-z0-9\-]{0,39}" className="rounded-md border bg-surface px-2 py-1" />
            </label>
            <label className="grid gap-1 text-sm">Project
              <input name="project" className="rounded-md border bg-surface px-2 py-1" />
            </label>
            <Button type="submit">Create code</Button>
          </form>
        </details>
      )}
    </div>
  )
}
