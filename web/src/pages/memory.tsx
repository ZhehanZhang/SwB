import { useEffect, useRef, useState } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { Search, X } from "lucide-react"
import { useSearchParams } from "react-router-dom"
import { PageHeaderActions } from "@/app/page-header-actions"
import { DataState, type DataStateProps } from "@/components/data-state"
import { Input } from "@/components/field"
import { LoadingSkeleton, Skeleton } from "@/components/loading-skeleton"
import { Button } from "@/components/ui/button"
import { sessionQuery } from "@/lib/api"
import { cn } from "@/lib/utils"
import { fetchMemoryList, type MemoryNote } from "./memory/api"
import { MemoryBackup } from "./memory/backup"
import { Time } from "@/components/time"
import { Author, scopeName } from "./memory/display"
import { MemorySync } from "./memory/header"
import { NoteInspector } from "./memory/inspector"

const pageSize = 50
const scopeGroups = [
  { label: "Shared", matches: (scope: string) => scope === "general" },
  { label: "Projects", matches: (scope: string) => scope.startsWith("project:") },
  { label: "Agents", matches: (scope: string) => scope.startsWith("agent:") },
]

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
        <col className="w-9/40" />
        <col className="w-193/400" />
        <col className="w-41/200" />
        <col className="w-7/80" />
      </colgroup>
      <thead role="rowgroup" className="sr-only lg:not-sr-only lg:table-header-group">
        <tr role="row" className="border-b text-label-sm font-medium text-muted-foreground">
          {["Key", "Summary", "Written by", "Updated"].map((label) => (
            <th role="columnheader" key={label} scope="col" className="pb-2 font-medium">{label}</th>
          ))}
        </tr>
      </thead>
      <tbody role="rowgroup" className="block lg:table-row-group">
        {state ? (
          <tr role="row" className="block lg:table-row">
            <td role="cell" colSpan={4} className="block lg:table-cell">
              {state.kind === "loading"
                ? <LoadingSkeleton message={state.message ?? "Loading…"}>{[0, 1, 2, 3].map(row => <div key={row} className="flex h-row items-center border-b"><Skeleton className="h-3 w-2/3" /></div>)}</LoadingSkeleton>
                : <DataState {...state} />}
            </td>
          </tr>
        ) : notes.map((note) => (
          <tr key={note.id} role="row" className="grid grid-cols-fill-auto gap-x-3 gap-y-1 border-b py-2.5 lg:table-row lg:h-row lg:py-0">
            <td role="cell" className="col-span-2 min-w-0 lg:pr-4">
              {note.key && <button
                className="block w-full truncate rounded-sm py-1 text-left font-mono text-mono-sm hover:underline focus-visible:outline-offset-1 lg:py-2"
                type="button"
                title={note.key}
                onClick={(event) => onOpen(note, event.currentTarget)}
              >
                {note.key}
              </button>}
            </td>
            <td role="cell" className="col-span-2 min-w-0 text-body-md text-muted-foreground lg:pr-4">
              {note.key ? <span className="block wrap-anywhere lg:truncate" title={note.summary}>{note.summary}</span> : (
                <button
                  className="block w-full rounded-sm text-left wrap-anywhere hover:underline focus-visible:outline-offset-1 lg:truncate"
                  type="button"
                  title={note.summary || note.title}
                  onClick={(event) => onOpen(note, event.currentTarget)}
                >
                  {note.summary || note.title}
                </button>
              )}
            </td>
            <td role="cell" className="min-w-0 text-body-sm lg:pr-4 lg:text-body-md"><Author source={note.writer} /></td>
            <td role="cell" className="self-center whitespace-nowrap text-body-sm text-muted-foreground"><Time value={note.updated_at} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function MemoryPage() {
  const { data: session } = useQuery(sessionQuery)
  const csrf = session?.signed_in ? session.csrf : null
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
  })).sort((a, b) => a.name.localeCompare(b.name))
  const showBackup = !!csrf && params.get("view") === "backup"
  const scope = params.get("scope") ?? "general"
  const active = counts.find(({ name }) => name === scope) ?? counts.find(({ name }) => name === "general")
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
    enabled: !!scopeCounts && !showBackup,
  })
  const rows = notes.data?.pages.flatMap((page) => page.items) ?? []
  const showMore = notes.hasNextPage && (!!query || rows.length < scopeCount)
  const noteTotal = query ? rows.length : scopeCount
  const resultText = query ? `${noteTotal} shown` : `${noteTotal} ${noteTotal === 1 ? "note" : "notes"}`
  const state: DataStateProps | undefined = !scopeCounts && scopes.isError
    ? { kind: "error", message: "Notes unavailable." }
    : notes.isPending ? { kind: "loading", message: "Loading notes…" }
    : notes.isError && !notes.data ? { kind: "error", message: "Notes unavailable.", onRetry: () => void notes.refetch() }
    : !rows.length ? { kind: "empty", message: query ? "0 results" : "0 notes" }
    : undefined

  useEffect(() => { if (searchOpen && !showBackup) searchInput.current?.focus() }, [searchOpen, showBackup])

  function selectScope(next: string, replace = false) {
    setParams((previous) => {
      const nextParams = new URLSearchParams(previous)
      nextParams.delete("view")
      if (next === "general") nextParams.delete("scope")
      else nextParams.set("scope", next)
      return nextParams
    }, { replace, preventScrollReset: true })
  }

  function selectBackup() {
    setSelected(null)
    setParams((previous) => {
      const nextParams = new URLSearchParams(previous)
      nextParams.set("view", "backup")
      return nextParams
    })
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
    <div className="grid min-w-0 gap-y-4 md:grid-cols-memory md:gap-x-6 lg:min-h-memory lg:grid-cols-memory-wide lg:gap-x-8">
      {csrf && <PageHeaderActions><MemorySync csrf={csrf} /></PageHeaderActions>}
      <nav aria-label="Memory scopes" className="space-y-3 border-b pb-3 md:border-r md:border-b-0 md:pr-4 md:pb-0 lg:-ml-2 lg:pt-2">
        {scopeCounts ? (
          scopeGroups.map(({ label, matches }) => {
            const group = counts.filter(({ name }) => matches(name))
            if (!group.length) return null
            const heading = `memory-scope-${label.toLowerCase()}`
            return (
              <section key={label} aria-labelledby={heading}>
                <h2 id={heading} className="px-2.5 text-caption font-medium text-muted-foreground">{label}</h2>
                <ul className="mt-0.5 grid gap-0.5">
                  {group.map(({ name, count }) => {
                    const pressed = !showBackup && activeScope === name
                    return (
                      <li key={name}>
                        <button type="button" aria-pressed={pressed}
                          className={cn("flex min-h-8 w-full items-center justify-between gap-3 rounded-md px-2.5 text-left text-body-sm hover:bg-muted",
                            pressed && "bg-muted")}
                          title={scopeName(name)} onClick={() => { if (!pressed) selectScope(name) }}>
                          <span className="truncate">{scopeName(name)}</span>
                          <span className="text-muted-foreground">{count}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </section>
            )
          })
        ) : scopes.isPending ? (
          <DataState kind="loading" message="Loading scopes…" className="px-2.5 py-2" />
        ) : <DataState kind="error" message="Scopes unavailable." onRetry={() => void scopes.refetch()} className="py-2" />}
        {csrf && (
          <div className="border-t pt-2">
            <button type="button" aria-pressed={showBackup}
              className={cn("flex min-h-8 w-full items-center rounded-md px-2.5 text-left text-label-sm hover:bg-muted", showBackup && "bg-muted")}
              onClick={() => { if (!showBackup) selectBackup() }}>Backup</button>
          </div>
        )}
      </nav>
      {showBackup && csrf ? <MemoryBackup csrf={csrf} /> : (
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
              {scopeCounts && state?.kind !== "empty" && <p aria-live="polite" className="text-body-sm text-muted-foreground">{!query || !notes.isPending ? resultText : ""}</p>}
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
              {notes.isFetchNextPageError && <span role="alert" className="text-body-sm text-muted-foreground">Loading more notes failed.</span>}
            </div>
          )}
        </section>
      )}
      {!showBackup && selected && (
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
