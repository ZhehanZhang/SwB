import { useState } from "react"
import { useQuery, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query"
import { Ellipsis } from "lucide-react"
import { DropdownMenu } from "radix-ui"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { isObject, requestJson, sessionQuery } from "@/lib/api"
import { type Conversation, type InboxData } from "./data"

const actions = { read: "Mark read", redeliver: "Re-deliver" }
type Action = keyof typeof actions
type Receipt = { cursor: number; count?: number | null }

function isActionResponse(value: unknown): value is { updated: string[] } {
  return isObject(value) && Array.isArray(value.updated) && value.updated.every((handle) => typeof handle === "string")
}

export function writeInboxAction(csrf: string, action: Action, handles: string[], receipt?: Receipt) {
  return requestJson("/api/inbox/actions", isActionResponse, {
    method: "POST", csrf, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, handles, ...receipt }),
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

export function canRun(action: Action, row: Conversation) {
  return action === "read" ? row.unread : row.owner === "owner" && row.destination === "mailbox"
    && (row.status === null || ["delivered", "stored", "notified", "notify_failed", "no_owner_surface"].includes(row.status))
}

export function useInboxActions() {
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const [pending, setPending] = useState<ReadonlyMap<string, Action>>(() => new Map())

  function run(action: Action, rows: Conversation[]) {
    const csrf = session?.csrf
    const handles = rows.filter((row) => canRun(action, row) && !pending.has(row.handle)).map((row) => row.handle).slice(0, 50)
    if (!csrf || !handles.length) return
    setPending((old) => new Map([...old, ...handles.map((handle) => [handle, action] as const)]))
    const clear = () => setPending((old) => new Map([...old].filter(([handle]) => !handles.includes(handle))))
    let settled = false
    async function commit() {
      if (settled || !csrf) return
      settled = true
      try {
        const result = await writeInboxAction(csrf, action, handles)
        if (action === "read") markInboxCacheRead(client, result.updated)
        else await Promise.all([
          client.invalidateQueries({ queryKey: ["inbox"] }),
          ...handles.map((handle) => client.invalidateQueries({ queryKey: ["conversation", handle] })),
        ])
        if (result.updated.length !== handles.length) toast.error("Some updates failed")
      } catch {
        toast.error("Inbox update failed")
      } finally {
        clear()
      }
    }
    toast(action === "read" ? "Marked read" : "Re-delivery queued", {
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
  const available = (Object.keys(actions) as Action[]).filter((action) => canRun(action, row))
  if (!available.length) return null
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
          {available.map((action) => (
            <DropdownMenu.Item key={action} onSelect={() => service.run(action, [row])}
              className="cursor-default rounded-sm px-2 py-1.5 text-sm outline-none data-highlighted:bg-muted">
              {actions[action]}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

export function BulkActions({ rows, actions: service }: { rows: Conversation[]; actions: InboxActions }) {
  return (
    <div className="flex flex-wrap gap-2">
      {(Object.keys(actions) as Action[]).map((action) => {
        const eligible = rows.filter((row) => canRun(action, row))
        return <Button key={action} size="sm" variant="outline" disabled={!service.enabled || !eligible.length}
          onClick={() => service.run(action, eligible)}>{actions[action]}</Button>
      })}
    </div>
  )
}
