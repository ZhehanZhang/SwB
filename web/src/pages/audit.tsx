import { useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { DataState } from "@/components/data-state"
import { Logo } from "@/components/logo"
import { Time } from "@/components/time"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/tabs"
import { Button } from "@/components/ui/button"
import { requestJson } from "@/lib/api"
import { auditActor, auditActorKind, auditGroups, auditProject, auditText, isAuditGroup, isAuditPage, type AuditGroup } from "./audit/events"

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
            <tr role="row" className="border-b text-body-sm text-muted-foreground">
              <th role="columnheader" scope="col" className="w-23/200 py-2 pr-3 font-medium">When</th>
              <th role="columnheader" scope="col" className="w-9/100 py-2 pr-3 font-medium">Project</th>
              <th role="columnheader" scope="col" className="w-4/25 py-2 pr-3 font-medium">Who</th>
              <th role="columnheader" scope="col" className="py-2 pr-3 font-medium">What</th>
              <th role="columnheader" scope="col" className="w-6/25 py-2 pr-3 font-medium">Detail</th>
              <th role="columnheader" scope="col" className="w-17/200 py-2 font-medium">Via</th>
            </tr>
          </thead>
          <tbody role="rowgroup" className="max-md:block">
            {items.map((item) => {
              const who = auditActor(item)
              return (
                <tr key={item.id} role="row" className="border-b text-body-sm max-md:grid max-md:grid-cols-fill-auto max-md:gap-x-3 max-md:gap-y-1 max-md:py-3 md:h-row">
                  <td role="cell" className="text-body-sm text-muted-foreground md:py-2 md:pr-3"><Time value={item.at} /></td>
                  <td role="cell" className="text-body-sm wrap-anywhere max-md:text-right md:py-2 md:pr-3">{auditProject(item)}</td>
                  <td role="cell" className="wrap-anywhere md:py-2 md:pr-3">
                    <span className="flex min-w-0 items-center gap-2">
                      {who && who !== "you" && <Logo kind={auditActorKind(item)} className="size-5" />}
                      {who}
                    </span>
                  </td>
                  <td role="cell" className="wrap-anywhere max-md:col-span-2 max-md:row-start-3 md:py-2 md:pr-3">
                    <span className="flex items-center gap-2">
                      {item.metadata?.target_avatar_url !== undefined && <Logo kind="captain" avatarUrl={item.metadata.target_avatar_url} className="size-5" />}
                      <span>{auditText(item)}</span>
                    </span>
                  </td>
                  <td role="cell" className="wrap-anywhere text-muted-foreground empty:max-md:hidden max-md:col-span-2 max-md:row-start-4 md:py-2 md:pr-3">
                    {item.detail}
                  </td>
                  <td role="cell" className="text-body-sm text-muted-foreground max-md:text-right md:py-2">
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
            ? <DataState kind="error" message="Audit events unavailable." onRetry={() => void audit.refetch()} />
            : <DataState kind="empty" message="0 audit events" />)}
        {audit.isFetchNextPageError && (
          <DataState kind="error" message="Loading more events failed." onRetry={() => void audit.fetchNextPage()} />
        )}
        {items.length > 0 && audit.isRefetchError && (
          <DataState kind="error" message="Audit refresh failed." onRetry={() => void audit.refetch()} />
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
