import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { DataState } from "@/components/data-state"
import { Field, Input } from "@/components/field"
import { Button } from "@/components/ui/button"
import { Sheet } from "@/components/ui/sheet"
import { sessionQuery } from "@/lib/api"
import { applyMemoryAction, fetchMemoryDetail, fetchMoveScopes, type MemoryAction, type MemoryNote } from "./api"
import { Author, NoteTime, scopeName } from "./display"

export function NoteInspector({ note, onClose, onRelocate }: {
  note: MemoryNote
  onClose: () => void
  onRelocate: (scope: string) => void
}) {
  const client = useQueryClient()
  const { data: session } = useQuery(sessionQuery)
  const csrf = session?.signed_in ? session.csrf : null
  const [action, setAction] = useState<MemoryAction["kind"] | null>(null)
  const [toScope, setToScope] = useState("")
  const [draft, setDraft] = useState({ title: "", body: "" })
  const [restoreRev, setRestoreRev] = useState<number | null>(null)
  const actionButtons = useRef<HTMLDivElement>(null)
  const confirmation = useRef<HTMLFormElement>(null)
  const trigger = useRef<{ button: HTMLButtonElement; kind: MemoryAction["kind"] }>(null)
  const titleInput = useRef<HTMLInputElement>(null)
  const detail = useQuery({
    queryKey: ["memory", "detail", note.id],
    queryFn: ({ signal }) => fetchMemoryDetail(note.id, signal),
  })
  const projects = useQuery({
    queryKey: ["memory", "destinations"],
    queryFn: ({ signal }) => fetchMoveScopes(signal),
    enabled: action === "move" && !!csrf,
  })
  const mutation = useMutation({
    mutationFn: (next: MemoryAction) => {
      if (!csrf) throw new Error("A CSRF token is required")
      return applyMemoryAction(note.id, next, csrf)
    },
    retry: false,
    onSuccess: async (_, saved) => {
      void client.invalidateQueries({ queryKey: ["memory", "scopes"] })
      void client.invalidateQueries({ queryKey: ["memory", "notes"] })
      if (saved.kind === "delete") {
        client.removeQueries({ queryKey: ["memory", "detail", note.id], exact: true })
        onClose()
        toast.success("Note deleted.")
      } else {
        if (saved.kind === "move" || saved.kind === "promote") {
          onRelocate(saved.kind === "move" ? saved.toScope : "general")
        }
        await client.invalidateQueries({ queryKey: ["memory", "detail", note.id], exact: true })
        setAction(null)
        setToScope("")
        setRestoreRev(null)
        toast.success({
          edit: "Note saved.",
          restore: "Revision restored.",
          promote: "Note promoted to general.",
          move: "Note moved.",
        }[saved.kind])
      }
    },
  })
  const entry = detail.data
  const scope = entry?.scope ?? note.scope
  const destinations = projects.data?.filter((destination) => destination !== scope) ?? []
  const validDestination = destinations.includes(toScope)

  useEffect(() => {
    if (action === "edit") titleInput.current?.focus()
    else if (action) confirmation.current?.focus()
    else if (trigger.current) {
      const { button, kind } = trigger.current
      if (button.isConnected) button.focus()
      else (actionButtons.current?.querySelector<HTMLButtonElement>(`[data-action="${kind}"]`)
        ?? actionButtons.current?.querySelector<HTMLButtonElement>("button"))?.focus()
      trigger.current = null
    }
  }, [action])

  function startAction(next: MemoryAction["kind"] | null, button?: HTMLButtonElement) {
    if (next && button) trigger.current = { button, kind: next }
    mutation.reset()
    setAction(next)
    setToScope("")
    if (next !== "restore") setRestoreRev(null)
    if (next === "edit" && entry) setDraft({ title: entry.title, body: entry.body })
  }

  return (
    <Sheet
      open
      title={entry?.title ?? note.title}
      onOpenChange={(open) => { if (!open && !mutation.isPending) onClose() }}
    >
      {detail.isPending ? <DataState kind="loading" message="Loading note…" />
        : detail.isError ? (
          <DataState kind="error" message="Note unavailable." onRetry={() => void detail.refetch()} />
        ) : entry && (
          <>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-2 border-b pb-4 text-xs">
              {entry.key && <>
                <dt className="text-muted-foreground">Key</dt>
                <dd className="font-mono break-all">{entry.key}</dd>
              </>}
              <dt className="text-muted-foreground">Path</dt>
              <dd className="font-mono wrap-anywhere">{entry.path}</dd>
              <dt className="text-muted-foreground">Scope</dt>
              <dd className="font-mono break-all">{scopeName(scope)}</dd>
              <dt className="text-muted-foreground">Namespace</dt>
              <dd className="font-mono break-all">{entry.namespace}</dd>
              <dt className="text-muted-foreground">Created by</dt>
              <dd><Author source={entry.created_by} /></dd>
              <dt className="text-muted-foreground">Changed by</dt>
              <dd><Author source={entry.content_by} /></dd>
              {entry.sync_source && <>
                <dt className="text-muted-foreground">Synced from</dt>
                <dd className="wrap-anywhere">{entry.sync_source}</dd>
              </>}
              <dt className="text-muted-foreground">Updated</dt>
              <dd><NoteTime date={entry.updated_at} /></dd>
              {!!entry.tags.length && <>
                <dt className="text-muted-foreground">Tags</dt>
                <dd className="break-words">{entry.tags.join(", ")}</dd>
              </>}
            </dl>
            {action === "edit" ? (
              <form className="space-y-3 pt-4" aria-label="Edit note" onSubmit={(event) => {
                event.preventDefault()
                if (draft.title.trim() && !mutation.isPending) mutation.mutate({ kind: "edit", ...draft })
              }}>
                <Field label="Title">
                  <Input ref={titleInput} required maxLength={200} value={draft.title} disabled={mutation.isPending}
                    onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
                </Field>
                <Field label="Body">
                  <textarea className="min-h-32 w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-body-md"
                    maxLength={20_000} value={draft.body} disabled={mutation.isPending}
                    onChange={(event) => setDraft({ ...draft, body: event.target.value })} />
                </Field>
                {mutation.isError && <p role="alert" className="text-sm text-destructive">Try saving the note again.</p>}
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" type="submit" disabled={!draft.title.trim() || mutation.isPending}>
                    {mutation.isPending ? "Saving…" : "Save"}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={mutation.isPending} onClick={() => startAction(null)}>Cancel</Button>
                </div>
              </form>
            ) : entry.body && <div className="py-4 text-sm leading-6 whitespace-pre-wrap [overflow-wrap:anywhere]">
              {entry.body}
            </div>}
            {action !== "edit" && !!entry.history.length && (
              <details className="border-t py-3">
                <summary className="cursor-pointer rounded-sm text-sm font-medium" onClick={(event) => {
                  if (action === "restore") {
                    event.preventDefault()
                    if (!mutation.isPending) startAction(null)
                  }
                }}>History</summary>
                <ul className="mt-2 divide-y text-xs">
                  {entry.history.map((revision) => (
                    <li key={revision.rev} className="py-2">
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0 space-y-1">
                          <p className="capitalize wrap-anywhere">{revision.action}</p>
                          <div className="flex flex-wrap gap-x-2 text-muted-foreground">
                            <span className="wrap-anywhere">{revision.source.startsWith("admin:") ? "Owner" : revision.source}</span>
                            <NoteTime date={revision.at} />
                          </div>
                        </div>
                        {csrf && <Button size="sm" variant="outline" disabled={!!action} onClick={(event) => {
                          startAction("restore", event.currentTarget)
                          setRestoreRev(revision.rev)
                        }}>Restore</Button>}
                      </div>
                      {action === "restore" && restoreRev === revision.rev && (
                        <form ref={confirmation} tabIndex={-1} aria-label="Confirm restore" className="mt-3 space-y-2"
                          onSubmit={(event) => {
                            event.preventDefault()
                            if (!mutation.isPending) mutation.mutate({ kind: "restore", rev: revision.rev })
                          }}>
                          <p className="text-sm">Restore revision?</p>
                          {mutation.isError && <p role="alert" className="text-sm text-destructive">Try restoring the revision again.</p>}
                          <div className="flex flex-wrap gap-2">
                            <Button type="submit" size="sm" disabled={mutation.isPending}>
                              {mutation.isPending ? "Restoring…" : "Restore revision"}
                            </Button>
                            <Button size="sm" variant="ghost" disabled={mutation.isPending} onClick={() => startAction(null)}>Cancel</Button>
                          </div>
                        </form>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {csrf && action !== "edit" && action !== "restore" && (
              <section aria-label="Note actions" className="border-t pt-4">
                {!action ? (
                  <div ref={actionButtons} className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="outline" data-action="edit" onClick={(event) => startAction("edit", event.currentTarget)}>Edit</Button>
                    {scope !== "general" && <Button size="sm" variant="outline" data-action="promote" onClick={(event) => startAction("promote", event.currentTarget)}>Promote</Button>}
                    <Button size="sm" variant="outline" data-action="move" onClick={(event) => startAction("move", event.currentTarget)}>Move</Button>
                    <Button size="sm" variant="ghost" data-action="delete" className="text-destructive" onClick={(event) => startAction("delete", event.currentTarget)}>Delete</Button>
                  </div>
                ) : (
                  <form ref={confirmation} tabIndex={-1} aria-label={`Confirm ${action}`} className="space-y-3" onSubmit={(event) => {
                    event.preventDefault()
                    if (mutation.isPending) return
                    if (action === "move") {
                      if (validDestination) mutation.mutate({ kind: "move", toScope })
                    } else mutation.mutate({ kind: action })
                  }}>
                    {action === "move" ? (
                      projects.isPending ? <DataState kind="loading" message="Loading projects…" className="py-0" />
                        : projects.isError ? (
                          <DataState kind="error" message="Projects unavailable." onRetry={() => void projects.refetch()} className="py-0" />
                        ) : destinations.length ? (
                          <Field label="Move to project">
                            <select
                              value={toScope}
                              disabled={mutation.isPending}
                              onChange={(event) => { setToScope(event.target.value); mutation.reset() }}
                              className="block min-h-9 w-full rounded-md border border-border-strong bg-surface px-2 text-body-md"
                            >
                              <option value="">Select a project</option>
                              {destinations.map((destination) => <option value={destination} key={destination}>{scopeName(destination)}</option>)}
                            </select>
                          </Field>
                        ) : <DataState kind="empty" message="0 other projects" className="py-0" />
                    ) : null}
                    {mutation.isError && (
                      <p role="alert" className="text-sm text-destructive">
                        {action === "delete" ? "Try deleting the note again." : action === "promote" ? "Try promoting the note again." : "Try moving the note again."}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="submit"
                        size="sm"
                        variant={action === "delete" ? "destructive" : "default"}
                        disabled={mutation.isPending || (action === "move" && !validDestination)}
                      >
                        {mutation.isPending ? "Saving…" : action === "delete" ? "Delete note" : action === "promote" ? "Promote to general" : "Move note"}
                      </Button>
                      <Button size="sm" variant="ghost" disabled={mutation.isPending} onClick={() => startAction(null)}>Cancel</Button>
                    </div>
                  </form>
                )}
              </section>
            )}
          </>
        )}
    </Sheet>
  )
}
