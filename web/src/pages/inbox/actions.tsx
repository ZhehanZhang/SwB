import { useState } from "react"
import { useQuery, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query"
import { Ellipsis } from "lucide-react"
import { DropdownMenu } from "radix-ui"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { isObject, requestJson, sessionQuery } from "@/lib/api"
import { type Conversation, type InboxData } from "./data"

type Receipt = { cursor: number; count?: number | null }

function isActionResponse(value: unknown): value is { updated: string[] } {
  return isObject(value) && Array.isArray(value.updated) && value.updated.every((handle) => typeof handle === "string")
}

export function markInboxRead(csrf: string, handles: string[], receipt?: Receipt) {
  return requestJson("/api/inbox/actions", isActionResponse, {
    method: "POST", csrf, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "read", handles, ...receipt }),
  })
}

export function markInboxCacheRead(client: QueryClient, updated: string[]) {
  const handles = new Set(updated)
  client.setQueriesData<InfiniteData<InboxData>>({ queryKey: ["inbox"] }, (previous) => previous && ({
    ...previous,
    pages: previous.pages.map((page) => ({
      ...page,
      conversations: page.conversations.map((row) => handles.has(row.handle) ? { ...row, unread: false } : row),
    })),
  }))
}

export function useInboxActions() {
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set())

  function run(rows: Conversation[]) {
    const csrf = session?.csrf
    const handles = rows.filter((row) => row.unread && !pending.has(row.handle)).map((row) => row.handle).slice(0, 50)
    if (!csrf || !handles.length) return
    setPending((old) => new Set([...old, ...handles]))
    const clear = () => setPending((old) => new Set([...old].filter((handle) => !handles.includes(handle))))
    let settled = false
    async function commit() {
      if (settled || !csrf) return
      settled = true
      try {
        const result = await markInboxRead(csrf, handles)
        markInboxCacheRead(client, result.updated)
        if (result.updated.length !== handles.length) toast.error("Some updates failed")
      } catch {
        toast.error("Inbox update failed")
      } finally {
        clear()
      }
    }
    toast("Marked read", {
      duration: 6000,
      onAutoClose: () => { void commit() },
      onDismiss: () => { void commit() },
      action: { label: "Undo", onClick: () => { settled = true; clear() } },
    })
  }

  return { run, pending, enabled: Boolean(session?.csrf) }
}

export type InboxActions = ReturnType<typeof useInboxActions>

export function RowActions({ row, actions: service }: { row: Conversation; actions: InboxActions }) {
  if (!row.unread) return null
  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild>
        <Button size="icon" variant="ghost" className="relative z-10 size-7" disabled={!service.enabled || service.pending.has(row.handle)}
          aria-label={`Actions for ${row.title || "message"}`}>
          <Ellipsis aria-hidden />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={3} className="z-50 min-w-28 rounded-md border border-border-strong bg-surface p-1 shadow-md">
          <DropdownMenu.Item onSelect={() => service.run([row])}
            className="cursor-default rounded-sm px-2 py-1.5 text-sm outline-none data-highlighted:bg-muted">
            Mark read
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

export function BulkActions({ rows, actions: service }: { rows: Conversation[]; actions: InboxActions }) {
  const eligible = rows.filter((row) => row.unread)
  return <Button size="sm" variant="outline" disabled={!service.enabled || !eligible.length}
    onClick={() => service.run(eligible)}>Mark read</Button>
}
