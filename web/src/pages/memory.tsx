import { useEffect, useRef, useState } from "react"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Search, X } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { DataState, type DataStateProps } from "@/components/data-state"
import { Field, Input } from "@/components/field"
import { Logo } from "@/components/logo"
import { Button } from "@/components/ui/button"
import { Sheet } from "@/components/ui/sheet"
import { sessionQuery } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  applyMemoryAction,
  fetchMemoryDetail,
  fetchMemoryList,
  fetchMoveScopes,
  type MemoryAction,
  type MemoryNote,
} from "./memory/api"

const pageSize = 50
const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" })
const fullDate = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" })

function scopeName(scope: string) {
  return scope.replace(/^project:/, "")
}

function noteName(note: MemoryNote) {
  return note.key || note.id
}

function NoteTime({ date, short = false }: { date: string | null; short?: boolean }) {
  if (!date) return "—"
  return <time dateTime={date} title={fullDate.format(new Date(date))}>{(short ? shortDate : fullDate).format(new Date(date))}</time>
}

function Author({ source }: { source: string }) {
  const name = source.startsWith("admin:") ? "Owner" : source
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Logo kind={source} className="size-5" />
      <span className="truncate" title={name}>{name || "Unknown"}</span>
    </span>
  )
}

function NotesTable({ scope, notes, state, onOpen }: {
  scope: string
  notes: MemoryNote[]
  state?: DataStateProps
  onOpen: (note: MemoryNote, trigger: HTMLButtonElement) => void
}) {
  return (
    <table role="table" className="block w-full text-left lg:table lg:table-fixed">
      <caption className="sr-only">Notes in {scopeName(scope)}</caption>
      <colgroup className="hidden lg:table-column-group">
        <col className="w-[22.5%]" />
        <col className="w-[48.25%]" />
        <col className="w-[20.5%]" />
        <col className="w-[8.75%]" />
      </colgroup>
      <thead role="rowgroup" className="sr-only lg:not-sr-only lg:table-header-group">
        <tr role="row" className="border-b text-[11px]/4 font-medium text-muted-foreground">
          {["Key", "Summary", "Written by", "Updated"].map((label) => (
            <th role="columnheader" key={label} scope="col" className="pb-2 font-medium">{label}</th>
          ))}
        </tr>
      </thead>
      <tbody role="rowgroup" className="block lg:table-row-group">
        {state ? (
          <tr role="row" className="block lg:table-row">
            <td role="cell" colSpan={4} className="block lg:table-cell"><DataState {...state} /></td>
          </tr>
        ) : notes.map((note) => (
          <tr key={note.id} role="row" className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-b py-2.5 lg:table-row lg:h-[43px] lg:py-0">
            <td role="cell" className="col-span-2 min-w-0 lg:pr-4">
              <button
                className="block w-full truncate rounded-sm py-1 text-left font-mono text-mono-sm hover:underline focus-visible:outline-offset-1 lg:py-2"
                type="button"
                title={noteName(note)}
                onClick={(event) => onOpen(note, event.currentTarget)}
              >
                {noteName(note)}
              </button>
            </td>
            <td role="cell" className="col-span-2 min-w-0 text-body-md text-muted-foreground lg:pr-4">
              <span className="block wrap-anywhere lg:truncate" title={note.summary}>{note.summary || "—"}</span>
            </td>
            <td role="cell" className="min-w-0 text-body-sm lg:pr-4 lg:text-body-md"><Author source={note.writer} /></td>
            <td role="cell" className="self-center whitespace-nowrap font-mono text-mono-sm text-muted-foreground"><NoteTime date={note.updated_at} short /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function NoteInspector({ note, onClose, onRelocate }: {
  note: MemoryNote
  onClose: () => void
  onRelocate: (scope: string) => void
}) {
  const client = useQueryClient()
  const { data: session } = useQuery(sessionQuery)
  const csrf = session?.signed_in ? session.csrf : null
  const [action, setAction] = useState<MemoryAction["kind"] | null>(null)
  const [toScope, setToScope] = useState("")
  const actionButtons = useRef<HTMLDivElement>(null)
  const confirmation = useRef<HTMLFormElement>(null)
  const triggeredAction = useRef<MemoryAction["kind"] | null>(null)
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
        onRelocate(saved.kind === "move" ? saved.toScope : "general")
        await client.invalidateQueries({ queryKey: ["memory", "detail", note.id], exact: true })
        setAction(null)
        setToScope("")
        toast.success(saved.kind === "promote" ? "Note promoted to general." : "Note moved.")
      }
    },
  })
  const entry = detail.data
  const scope = entry?.scope ?? note.scope
  const destinations = projects.data?.filter((destination) => destination !== scope) ?? []
  const validDestination = destinations.includes(toScope)

  useEffect(() => {
    if (action) confirmation.current?.focus()
    else if (triggeredAction.current) {
      const buttons = actionButtons.current
      const trigger = buttons?.querySelector<HTMLButtonElement>(`[data-action="${triggeredAction.current}"]`) ?? buttons?.querySelector("button")
      trigger?.focus()
      triggeredAction.current = null
    }
  }, [action])

  function startAction(next: MemoryAction["kind"] | null) {
    if (next) triggeredAction.current = next
    mutation.reset()
    setAction(next)
    setToScope("")
  }

  return (
    <Sheet
      open
      title={entry?.title ?? note.title}
      description={`${scopeName(scope)} / ${entry?.namespace ?? note.namespace}`}
      onOpenChange={(open) => { if (!open && !mutation.isPending) onClose() }}
    >
      {detail.isPending ? <DataState kind="loading" message="Loading note…" />
        : detail.isError ? (
          <DataState kind="error" message="Couldn’t load this note." onRetry={() => void detail.refetch()} />
        ) : entry && (
          <>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-2 border-b pb-4 text-xs">
              <dt className="text-muted-foreground">Key</dt>
              <dd className="font-mono break-all">{noteName(entry)}</dd>
              <dt className="text-muted-foreground">Written by</dt>
              <dd><Author source={entry.writer} /></dd>
              <dt className="text-muted-foreground">Updated</dt>
              <dd><NoteTime date={entry.updated_at} /></dd>
              {!!entry.tags.length && <>
                <dt className="text-muted-foreground">Tags</dt>
                <dd className="break-words">{entry.tags.join(", ")}</dd>
              </>}
            </dl>
            <div className="py-4 text-sm leading-6 whitespace-pre-wrap [overflow-wrap:anywhere]">
              {entry.body || <span className="text-muted-foreground">No note body.</span>}
            </div>
            {!!entry.history.length && (
              <details className="border-t py-3">
                <summary className="cursor-pointer rounded-sm text-sm font-medium">History</summary>
                <ul className="mt-2 divide-y text-xs">
                  {entry.history.map((revision, index) => (
                    <li key={index} className="space-y-1 py-2">
                      <p className="break-words">{revision.action}</p>
                      <p className="break-words text-muted-foreground">{revision.source.startsWith("admin:") ? "Owner" : revision.source}</p>
                      {revision.at && <p className="text-muted-foreground"><NoteTime date={revision.at} /></p>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {csrf && (
              <section aria-label="Note actions" className="border-t pt-4">
                {!action ? (
                  <div ref={actionButtons} className="flex flex-wrap items-center gap-2">
                    {scope !== "general" && <Button size="sm" variant="outline" data-action="promote" onClick={() => startAction("promote")}>Promote</Button>}
                    <Button size="sm" variant="outline" data-action="move" onClick={() => startAction("move")}>Move</Button>
                    <Button size="sm" variant="ghost" className="text-destructive" data-action="delete" onClick={() => startAction("delete")}>Delete</Button>
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
                          <DataState kind="error" message="Couldn’t load projects." onRetry={() => void projects.refetch()} className="py-0" />
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
                        ) : <DataState kind="empty" message="No other projects." className="py-0" />
                    ) : (
                      <p className="text-sm">
                        {action === "promote" ? "Promote this note to general? All projects can read it there."
                          : "Delete this note? It will no longer appear in Memory."}
                      </p>
                    )}
                    {mutation.isError && (
                      <p role="alert" className="text-sm text-destructive">
                        {action === "delete" ? "Couldn’t delete this note." : action === "promote" ? "Couldn’t promote this note." : "Couldn’t move this note."}
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

export function MemoryPage() {
  const [params, setParams] = useSearchParams()
  const [selected, setSelected] = useState<MemoryNote | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchText, setSearchText] = useState("")
  const [query, setQuery] = useState("")
  const searchButton = useRef<HTMLButtonElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const openedFrom = useRef<HTMLElement | null>(null)
  const notesHeading = useRef<HTMLHeadingElement>(null)
  const scopes = useQuery({
    queryKey: ["memory", "scopes"],
    queryFn: ({ signal }) => fetchMemoryList({ limit: "1" }, signal),
  })
  const scopeCounts = scopes.data?.scopes
  const counts = Object.entries(scopeCounts ?? {}).map(([name, namespaces]) => ({
    name, count: Object.values(namespaces).reduce((total, value) => total + value, 0),
  })).sort((a, b) => a.name === b.name ? 0 : a.name === "general" ? -1 : b.name === "general" ? 1 : a.name.localeCompare(b.name))
  const scope = params.get("scope") ?? "general"
  const active = counts.find(({ name }) => name === scope) ?? counts[0]
  const activeScope = active?.name ?? "general"
  const scopeCount = active?.count ?? 0
  const notes = useInfiniteQuery({
    queryKey: ["memory", "notes", activeScope, query],
    queryFn: ({ pageParam, signal }) => fetchMemoryList({
      scope: activeScope,
      limit: String(pageSize),
      ...(pageParam > 0 && { offset: String(pageParam) }),
      ...(query && { q: query }),
    }, signal),
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    enabled: !!scopeCounts,
  })
  const rows = notes.data?.pages.flatMap((page) => page.items) ?? []
  const showMore = notes.hasNextPage && (!!query || rows.length < scopeCount)
  const noteTotal = query ? rows.length : scopeCount
  const resultText = query ? `${noteTotal} shown` : `${noteTotal} ${noteTotal === 1 ? "note" : "notes"}`
  const state: DataStateProps | undefined = !scopeCounts && scopes.isError
    ? { kind: "error", message: "Couldn’t load notes." }
    : notes.isPending ? { kind: "loading", message: "Loading notes…" }
    : notes.isError && !notes.data ? { kind: "error", message: "Couldn’t load notes.", onRetry: () => void notes.refetch() }
    : !rows.length ? { kind: "empty", message: query ? "No matching notes." : "No notes in this scope." }
    : undefined

  useEffect(() => { if (searchOpen) searchInput.current?.focus() }, [searchOpen])

  function selectScope(next: string, replace = false) {
    setParams((previous) => {
      const nextParams = new URLSearchParams(previous)
      if (next === "general") nextParams.delete("scope")
      else nextParams.set("scope", next)
      return nextParams
    }, { replace, preventScrollReset: true })
  }

  function closeSearch() {
    setSearchOpen(false)
    setSearchText("")
    setQuery("")
    searchButton.current?.focus()
  }

  function closeNote() {
    setSelected(null)
    requestAnimationFrame(() => {
      if (openedFrom.current?.isConnected) openedFrom.current.focus()
      else notesHeading.current?.focus()
    })
  }

  return (
    <div className="grid min-w-0 gap-y-4 md:grid-cols-[176px_minmax(0,1fr)] md:gap-x-6 lg:min-h-[calc(100dvh-var(--topbar-height)-3rem)] lg:grid-cols-[208px_minmax(0,1fr)] lg:gap-x-8">
      <aside aria-label="Memory scopes" className="border-b pb-3 md:border-r md:border-b-0 md:pr-4 md:pb-0 lg:-mb-8 lg:-ml-2 lg:pt-2">
        {scopeCounts ? (
          <ul className="grid gap-0.5">
            {counts.map(({ name, count }) => (
              <li key={name}>
                <button
                  type="button"
                  aria-pressed={activeScope === name}
                  className={cn(
                    "flex min-h-8 w-full items-center justify-between gap-3 rounded-md px-2.5 text-left font-mono text-mono-sm hover:bg-muted",
                    activeScope === name && "bg-muted",
                  )}
                  title={scopeName(name)}
                  onClick={() => { if (activeScope !== name) selectScope(name) }}
                >
                  <span className="truncate">{scopeName(name)}</span>
                  <span className="text-muted-foreground">{count}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : scopes.isPending ? (
          <DataState kind="loading" message="Loading scopes…" className="px-2.5 py-2" />
        ) : <DataState kind="error" message="Couldn’t load scopes." onRetry={() => void scopes.refetch()} className="py-2" />}
      </aside>
      <section aria-labelledby="memory-notes-heading" className="min-w-0 lg:pt-2">
        <div className="mb-1 flex min-h-7 items-center justify-between gap-4">
          <h2 id="memory-notes-heading" ref={notesHeading} tabIndex={-1} className="min-w-0 truncate text-heading-md">
            {scopeName(activeScope)}
          </h2>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              ref={searchButton}
              variant="ghost"
              size="icon"
              className="size-7 text-muted-foreground"
              aria-label="Search notes"
              aria-expanded={searchOpen}
              aria-controls={searchOpen ? "memory-search" : undefined}
              title="Search notes"
              onClick={() => searchOpen ? closeSearch() : setSearchOpen(true)}
            >
              <Search aria-hidden strokeWidth={1.5} />
            </Button>
            {scopeCounts && <p aria-live="polite" className="text-body-sm text-muted-foreground">{!query || !notes.isPending ? resultText : ""}</p>}
          </div>
        </div>
        {searchOpen && (
          <form
            id="memory-search"
            role="search"
            aria-label="Memory"
            className="mb-3 flex items-center gap-2"
            onSubmit={(event) => { event.preventDefault(); setQuery(searchText.trim()) }}
            onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); closeSearch() } }}
          >
            <Input
              ref={searchInput}
              type="search"
              value={searchText}
              maxLength={500}
              onChange={(event) => {
                setSearchText(event.target.value)
                if (!event.target.value.trim()) setQuery("")
              }}
              aria-label={`Search notes in ${scopeName(activeScope)}`}
              placeholder="Search notes"
              className="min-w-0 flex-1 sm:max-w-70"
            />
            <Button type="submit" size="sm" variant="outline">Search</Button>
            <Button variant="ghost" size="icon" className="size-9" aria-label="Close search" onClick={closeSearch}>
              <X aria-hidden strokeWidth={1.5} />
            </Button>
          </form>
        )}
        <NotesTable scope={activeScope} notes={rows} state={state} onOpen={(note, trigger) => {
          openedFrom.current = trigger
          setSelected(note)
        }} />
        {showMore && (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button variant="outline" size="sm" disabled={notes.isFetchingNextPage} onClick={() => void notes.fetchNextPage()}>
              {notes.isFetchingNextPage ? "Loading…" : notes.isFetchNextPageError ? "Retry" : "Load more"}
            </Button>
            {notes.isFetchNextPageError && <span role="alert" className="text-sm text-muted-foreground">Couldn’t load more notes.</span>}
          </div>
        )}
      </section>
      {selected && (
        <NoteInspector
          key={selected.id}
          note={selected}
          onClose={closeNote}
          onRelocate={(destination) => selectScope(destination, true)}
        />
      )}
    </div>
  )
}
