import { useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { DataState } from "@/components/data-state"
import { Logo } from "@/components/logo"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/tabs"
import { Button } from "@/components/ui/button"
import { requestJson } from "@/lib/api"
import { auditActor, auditGroups, auditProject, auditText, isAuditGroup, isAuditPage, type AuditGroup } from "./audit/events"

const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" })
const clock = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" })

function EventTime({ value }: { value: string | null }) {
  const at = value ? new Date(value) : null
  if (!at || !Number.isFinite(at.getTime())) return "—"
  return <time dateTime={at.toISOString()} title={at.toLocaleString()}>{date.format(at)} {clock.format(at)}</time>
}

export function AuditPage() {
  const [group, setGroup] = useState<AuditGroup>("All")
  const audit = useInfiniteQuery({
    queryKey: ["audit", group],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ limit: "50" })
      const actions = auditGroups[group]
      if (actions.length) params.set("actions", actions.join(","))
      if (pageParam !== null) params.set("cursor", String(pageParam))
      return requestJson(`/api/audit?${params}`, isAuditPage, { signal })
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
  const items = audit.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <Tabs className="pt-2" value={group} onValueChange={(value) => { if (isAuditGroup(value)) setGroup(value) }}>
      <TabsList aria-label="Audit actions" className="max-w-full gap-0.5 p-0.5">
        {Object.keys(auditGroups).map((name) => (
          <TabsTrigger key={name} value={name} className="px-2.5 hover:text-foreground sm:px-3">
            {name}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value={group}>
        <table role="table" className="w-full text-left max-md:block md:table-fixed">
          <caption className="sr-only">Audit events</caption>
          <thead role="rowgroup" className="sr-only md:not-sr-only">
            <tr role="row" className="border-b text-[11px] leading-4 text-muted-foreground">
              <th role="columnheader" scope="col" className="w-[11.5%] py-2 pr-3 font-medium">When</th>
              <th role="columnheader" scope="col" className="w-[9%] py-2 pr-3 font-medium">Project</th>
              <th role="columnheader" scope="col" className="w-[16%] py-2 pr-3 font-medium">Who</th>
              <th role="columnheader" scope="col" className="py-2 pr-3 font-medium">What</th>
              <th role="columnheader" scope="col" className="w-[8.5%] py-2 font-medium">Via</th>
            </tr>
          </thead>
          <tbody role="rowgroup" className="max-md:block">
            {items.map((item) => {
              const who = auditActor(item)
              return (
                <tr key={item.id} role="row" className="border-b text-body-sm max-md:grid max-md:grid-cols-[minmax(0,1fr)_auto] max-md:gap-x-3 max-md:gap-y-1 max-md:py-3 md:h-[43px]">
                  <td role="cell" className="font-mono text-mono-sm text-muted-foreground md:py-2 md:pr-3"><EventTime value={item.at} /></td>
                  <td role="cell" className="font-mono text-mono-sm wrap-anywhere max-md:text-right md:py-2 md:pr-3">{auditProject(item) ?? "—"}</td>
                  <td role="cell" className="wrap-anywhere md:py-2 md:pr-3">
                    <span className="flex min-w-0 items-center gap-2">
                      {who !== "you" && who !== "—" && <Logo kind={item.origin === "slack" || item.origin === "github" ? item.origin : who} className="size-5" />}
                      {who}
                    </span>
                  </td>
                  <td role="cell" className="wrap-anywhere max-md:col-span-2 max-md:row-start-3 md:py-2 md:pr-3">{auditText(item)}</td>
                  <td role="cell" className="font-mono text-mono-sm text-muted-foreground max-md:text-right md:py-2">
                    {item.origin === "http" && who === "you" ? "web" : item.origin}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!items.length && (audit.isPending
          ? <DataState kind="loading" message="Loading audit…" />
          : audit.isError
            ? <DataState kind="error" message="Couldn’t load audit events." onRetry={() => void audit.refetch()} />
            : <DataState kind="empty" message="No audit events." />)}
        {audit.isFetchNextPageError && (
          <DataState kind="error" message="Couldn’t load more events." onRetry={() => void audit.fetchNextPage()} />
        )}
        {items.length > 0 && audit.isRefetchError && (
          <DataState kind="error" message="Couldn’t refresh audit events." onRetry={() => void audit.refetch()} />
        )}
        {audit.hasNextPage && !audit.isFetchNextPageError && (
          <Button variant="outline" size="sm" className="mt-4" disabled={audit.isFetching} onClick={() => void audit.fetchNextPage()}>
            {audit.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        )}
      </TabsContent>
    </Tabs>
  )
}
