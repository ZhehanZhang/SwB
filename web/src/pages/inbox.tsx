import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from "react"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, ArrowRight, Check, Play, X } from "lucide-react"
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import captainMark from "../../../site/public/marks/captain.webp?no-inline"
import { DataState } from "@/components/data-state"
import { ListRow } from "@/components/list-row"
import { LoadingSkeleton, Skeleton } from "@/components/loading-skeleton"
import { Logo } from "@/components/logo"
import { Button } from "@/components/ui/button"
import { requestJson, sessionQuery } from "@/lib/api"
import { displayReference } from "@/lib/display-reference"
import { cn } from "@/lib/utils"
import { BulkActions, markInboxCacheRead, markInboxRead, RowActions, useInboxActions, type InboxActions } from "./inbox/actions"
import {
  approvalTitle, channelLabel, isConversationPage, isInbox, isResumed, isWriteDecision, participants, principalLabel, senderLabel, senderLogoKind,
  type Conversation, type ConversationItem, type Pause, type PendingWrite,
} from "./inbox/data"
import { InboxFilters, inboxQuery } from "./inbox/filters"

const inboxKey = ["inbox"] as const
const itemLimit = 100

function conversationLink(handle: string, search: string) {
  return `/inbox/${encodeURIComponent(handle)}${search}`
}

type OpenConversation = (event: MouseEvent<HTMLAnchorElement>) => void

function primaryClick(event: MouseEvent<HTMLAnchorElement>) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !event.defaultPrevented
}

function relativeTime(value: string | null) {
  const at = value ? new Date(value) : null
  if (!at || !Number.isFinite(at.getTime())) return null
  const minutes = Math.max(0, Math.floor((Date.now() - at.getTime()) / 60_000))
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)
  return {
    at,
    short: days ? `${days} d` : hours ? `${hours} h` : minutes ? `${minutes} min` : "Now",
  }
}

function RelativeTime({ value }: { value: string | null }) {
  const time = relativeTime(value)
  if (!time) return null
  return <time dateTime={time.at.toISOString()} title={time.at.toLocaleString()}>{time.short}</time>
}

function Person({ label, kind, className, avatarUrl }: { label: string; kind?: string | null; className?: string; avatarUrl?: string | null }) {
  const isCaptain = kind === "captain" || kind === "capy" || kind === "capy_thread"
  return (
    <span className={cn("flex min-w-0 items-center gap-2", className)}>
      {isCaptain
        ? <img alt="" src={captainMark} className="size-5 shrink-0 rounded-md border border-border object-contain" />
        : <Logo kind={kind || label} avatarUrl={avatarUrl} className="size-5" fallbackLetter={label[0]} />}
      <span className="min-w-0 wrap-anywhere">{label}</span>
    </span>
  )
}

function ProjectName({ project }: { project: string | null }) {
  return project || <span role="img" aria-label="Unassigned" className="before:content-unassigned" />
}

function NeedsRow({ name, kind, title, at, preview, actions, avatarUrl }: {
  name: string
  kind?: string
  avatarUrl?: string | null
  title: ReactNode
  at: string | null
  preview?: ReactNode
  actions: ReactNode
}) {
  return (
    <li className="grid grid-cols-1 gap-3 border-b py-3.5 md:grid-cols-fill-auto md:items-center md:py-3.75">
      <div className="grid min-w-0 grid-cols-auto-fill-auto items-center gap-x-2 gap-y-2.5 text-body-sm md:grid-cols-auto-auto-main md:gap-y-0.5">
        <Logo kind={kind || name} avatarUrl={avatarUrl} className="col-start-1 row-start-1 size-5 md:row-span-2 md:size-7" />
        <span className="col-start-2 row-start-1 max-w-56 min-w-0 font-semibold wrap-anywhere">{name}</span>
        <span className="col-start-3 row-start-1 font-mono text-mono-sm text-muted-foreground md:hidden"><RelativeTime value={at} /></span>
        <div className="col-span-3 row-start-2 min-w-0 wrap-anywhere md:col-span-1 md:col-start-3 md:row-start-1">
          {title}
          <span className="ml-2 hidden text-nav-label text-muted-foreground md:inline"><RelativeTime value={at} /></span>
        </div>
        {preview && <div className="col-span-3 row-start-3 -mt-1.5 max-h-40 overflow-y-auto wrap-anywhere whitespace-pre-wrap text-muted-foreground md:col-span-2 md:col-start-2 md:row-start-2 md:mt-0">{preview}</div>}
      </div>
      <div className="flex gap-2 max-md:[&>button]:flex-1 md:gap-3">{actions}</div>
    </li>
  )
}

function SlackApproval({ item }: { item: PendingWrite }) {
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const decide = useMutation({
    mutationFn: (verb: "approve" | "reject") => requestJson(
      `/api/slack/requests/${encodeURIComponent(item.id)}/${verb}`, isWriteDecision,
      { method: "POST", csrf: session?.csrf },
    ),
    onSuccess: ({ status }) => {
      if (status === "failed") toast.error("Slack delivery failed.")
      else if (status === "rejected") toast.success("Denied Slack request.")
      else toast.success("Approved Slack request.")
    },
    onError: () => toast.error("Slack request update failed."),
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: inboxKey }),
      client.invalidateQueries({ queryKey: ["audit"] }),
    ]),
  })
  const disabled = !session?.csrf || decide.isPending || decide.isSuccess
  return (
    <NeedsRow
      name={senderLabel(item.sender) || "Slack"}
      kind={senderLogoKind(item.sender) || "slack"}
      avatarUrl={item.sender?.avatar_url}
      title={approvalTitle(item)}
      at={item.created_at}
      preview={item.text ? `“${item.text}”` : item.name || undefined}
      actions={(
        <>
          <Button
            size="sm" variant="ghost" className="min-h-9 border border-border-strong md:min-h-control-sm md:border-transparent"
            disabled={disabled} onClick={() => decide.mutate("reject")}
          >
            <X aria-hidden className="max-md:hidden" />{decide.isPending && decide.variables === "reject" ? "Denying…" : "Deny"}
          </Button>
          <Button
            size="sm" className="min-h-9 md:min-h-control-sm" disabled={disabled} onClick={() => decide.mutate("approve")}
          >
            <Check aria-hidden />{decide.isPending && decide.variables === "approve" ? "Approving…" : "Approve"}
          </Button>
        </>
      )}
    />
  )
}

function TurnPause({ pause, search, fromList, onOpen }: { pause: Pause; search: string; fromList: boolean; onOpen: OpenConversation }) {
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const resume = useMutation({
    mutationFn: () => requestJson(`/api/inbox/pauses/${encodeURIComponent(pause.root)}/resume`, isResumed, {
      method: "POST", csrf: session?.csrf,
    }),
    onSuccess: () => toast.success("Resumed conversation."),
    onError: () => toast.error("Conversation failed to resume."),
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: inboxKey }),
      client.invalidateQueries({ queryKey: ["conversation", pause.root] }),
      client.invalidateQueries({ queryKey: ["audit"] }),
    ]),
  })
  const { conversation } = pause
  return (
    <NeedsRow
      name={displayReference(conversation?.title || "Conversation")}
      kind={conversation?.destination_kind || conversation?.destination}
      avatarUrl={conversation?.target_avatar_url ?? null}
      title={<Link className="hover:underline" to={conversationLink(pause.root, search)} state={{ fromList }} onClick={onOpen}>Paused at turn cap</Link>}
      at={pause.paused_at}
      preview={`${pause.turns} ${pause.turns === 1 ? "turn" : "turns"}`}
      actions={(
        <Button
          size="sm" className="min-h-9 md:min-h-control-sm" disabled={!session?.csrf || resume.isPending || resume.isSuccess}
          onClick={() => resume.mutate()}
        >
          <Play aria-hidden />{resume.isPending ? "Resuming…" : "Resume"}
        </Button>
      )}
    />
  )
}

function Conversations({ items, search, active, onOpen, actions, selected, onSelect }: {
  items: Conversation[]; search: string; active?: string; onOpen: OpenConversation; actions: InboxActions;
  selected?: ReadonlySet<string>; onSelect: (handle: string) => void;
}) {
  return (
    <table role="table" className="mt-1 w-full text-left @max-inbox-table/list:block @min-inbox-table/list:table-fixed">
      <caption className="sr-only">Conversations</caption>
      <thead role="rowgroup" className="sr-only @min-inbox-table/list:not-sr-only">
        <tr role="row" className="border-b text-caption text-muted-foreground">
          {selected && <th role="columnheader" scope="col" className="w-7"><span className="sr-only">Select</span></th>}
          <th role="columnheader" className="w-1/10 py-2 pr-2 font-medium" scope="col">Project</th>
          <th role="columnheader" className="w-4/25 py-2 pr-2 font-medium" scope="col">From</th>
          <th role="columnheader" className="w-4/25 py-2 pr-2 font-medium" scope="col">To</th>
          <th role="columnheader" className="py-2 pr-3 font-medium" scope="col">Subject</th>
          <th role="columnheader" className="w-11/100 py-2 pr-2 font-medium" scope="col">Status</th>
          <th role="columnheader" className="w-37/400 py-2 pr-3 font-medium" scope="col">Updated</th>
          <th role="columnheader" className="w-7" scope="col"><span className="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody role="rowgroup" className="@max-inbox-table/list:block">
        {items.map((row) => {
          const { from, to, fromKind, toKind } = participants(row)
          return (
            <tr key={row.handle} role="row" data-selected={active === row.handle || selected?.has(row.handle) || undefined} className="relative border-b text-body-sm focus-within:bg-muted/40 hover:bg-muted/40 data-selected:bg-muted @max-inbox-table/list:grid @max-inbox-table/list:grid-cols-fill-fill-auto @max-inbox-table/list:gap-x-2 @max-inbox-table/list:gap-y-1 @max-inbox-table/list:py-3.5 @min-inbox-table/list:h-row">
              {selected && (
                <td role="cell" className="@max-inbox-table/list:col-start-3 @max-inbox-table/list:row-start-3 @max-inbox-table/list:justify-self-end">
                  <input type="checkbox" aria-label={`Select ${row.title || "message"}`} checked={selected.has(row.handle)}
                    disabled={!row.unread || (!selected.has(row.handle) && selected.size >= 50)}
                    className="relative z-10 size-4 cursor-pointer align-middle accent-primary"
                    onChange={() => onSelect(row.handle)} />
                </td>
              )}
              <td role="cell" className="font-mono text-mono-sm wrap-anywhere @max-inbox-table/list:col-start-1 @max-inbox-table/list:row-start-3 @min-inbox-table/list:py-2 @min-inbox-table/list:pr-2"><ProjectName project={row.project} /></td>
              <td role="cell" className="@max-inbox-table/list:col-start-1 @max-inbox-table/list:row-start-2 @min-inbox-table/list:py-2 @min-inbox-table/list:pr-2"><Person label={from} kind={fromKind} avatarUrl={row.first_sender?.avatar_url} /></td>
              <td role="cell" className="@max-inbox-table/list:col-start-2 @max-inbox-table/list:row-start-2 @min-inbox-table/list:py-2 @min-inbox-table/list:pr-2"><Person label={to} kind={toKind} avatarUrl={row.target_avatar_url ?? undefined} /></td>
              <td role="cell" className="wrap-anywhere @max-inbox-table/list:col-span-2 @max-inbox-table/list:row-start-1 @max-inbox-table/list:font-semibold @min-inbox-table/list:py-2 @min-inbox-table/list:pr-3">
                <Link to={conversationLink(row.handle, search)} state={{ fromList: !active }} onClick={onOpen} title={row.handle}
                  aria-current={active === row.handle ? "page" : undefined}
                  className={cn(!selected && "after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:outline-ring", row.unread && "font-medium")}>
                  {displayReference(row.title || "Message")}
                </Link>
                {row.for_owner && <span className="ml-2 text-label-sm font-normal text-muted-foreground">For owner</span>}
              </td>
              <td role="cell" className="text-label-sm capitalize text-muted-foreground @max-inbox-table/list:col-start-2 @max-inbox-table/list:row-start-3 @max-inbox-table/list:text-right @min-inbox-table/list:py-2 @min-inbox-table/list:pr-2">{row.status?.replaceAll("_", " ")}</td>
              <td role="cell" className="font-mono text-mono-sm text-muted-foreground @max-inbox-table/list:col-start-3 @max-inbox-table/list:row-start-1 @min-inbox-table/list:py-2 @min-inbox-table/list:pr-2">
                <RelativeTime value={row.updated_at ?? row.created_at} />
              </td>
              <td role="cell" className="min-h-7 text-right @max-inbox-table/list:col-start-3 @max-inbox-table/list:row-start-2">
                <RowActions row={row} actions={actions} />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function InboxList({ active, onOpen }: { active?: string; onOpen: OpenConversation }) {
  const { search } = useLocation()
  const [params] = useSearchParams()
  const filterQuery = inboxQuery(params).toString()
  const actions = useInboxActions()
  const [selection, select] = useState<{ query: string; handles: Set<string> } | null>(null)
  const selected = selection?.query === filterQuery ? selection.handles : undefined
  const inbox = useInfiniteQuery({
    queryKey: [...inboxKey, filterQuery],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => {
      const query = new URLSearchParams(filterQuery)
      if (pageParam) query.set("cursor", pageParam)
      const suffix = query.size ? `?${query}` : ""
      return requestJson(`/api/inbox${suffix}`, isInbox, { signal })
    },
    getNextPageParam: (page) => page.next_cursor,
    refetchOnMount: false,
    refetchInterval: active ? false : 30_000,
  })
  const data = inbox.data?.pages[0]
  const conversations = (inbox.data?.pages.flatMap((page) => page.conversations) ?? []).map((row) => {
    return actions.pending.has(row.handle) ? { ...row, unread: false } : row
  }).filter((row) => params.get("unread") !== "1" || row.unread)
  const notice = params.get("notice")
  const status = notice === "decide-refused" ? "Request closed" : notice === "send-failed" ? "Slack delivery failed"
    : notice === "approved" ? "Approved Slack request" : notice === "rejected" ? "Denied Slack request" : null
  return (
    <div className="space-y-8 pt-2 md:space-y-8.5">
      <section aria-labelledby="needs-you">
        <h2 id="needs-you" className="text-heading-md font-semibold">Needs you</h2>
        {status && <p role="status" className="mt-2 text-body-sm">{status}</p>}
        {inbox.isPending && <div className="flex h-15 items-center"><Skeleton className="h-3 w-28" /></div>}
        {inbox.isError && !data && <DataState kind="error" message="Inbox unavailable." onRetry={() => void inbox.refetch()} />}
        {data && (data.pending_writes.length || data.pauses.length ? (
          <ul className="mt-2">
            {data.pending_writes.map((item) => <SlackApproval key={item.id} item={item} />)}
            {data.pauses.map((pause) => <TurnPause key={pause.root} pause={pause} search={search} onOpen={onOpen} fromList={!active} />)}
          </ul>
        ) : <DataState kind="empty" message="All clear" />)}
      </section>
      {(inbox.isPending || data) && (
        <section aria-labelledby="conversations">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 id="conversations" className="text-heading-md font-semibold">Conversations</h2>
            {conversations.length > 0 && <Button variant="ghost" size="sm" aria-pressed={Boolean(selected)}
              onClick={() => select(selected ? null : { query: filterQuery, handles: new Set() })}>{selected ? "Done" : "Select"}</Button>}
          </div>
          <InboxFilters />
          {selected && <div className="mt-2 flex flex-wrap justify-between gap-2">
            <BulkActions rows={conversations.filter((row) => selected.has(row.handle))} actions={actions} />
            <Button variant="ghost" size="sm" disabled={!selected.size} onClick={() => select({ query: filterQuery, handles: new Set() })}>Clear selection</Button>
          </div>}
          {inbox.isPending ? <ConversationsSkeleton /> : conversations.length
            ? <Conversations items={conversations} search={search} active={active} onOpen={onOpen} actions={actions} selected={selected} onSelect={(handle) => {
              if (!selected) return
              const next = new Set(selected)
              if (next.has(handle)) next.delete(handle)
              else next.add(handle)
              select({ query: filterQuery, handles: next })
            }} />
            : <DataState kind="empty" message="0 conversations" />}
          {inbox.hasNextPage && (
            <Button className="mt-4" variant="outline" size="sm" disabled={inbox.isFetching} onClick={() => void inbox.fetchNextPage()}>
              {inbox.isFetchingNextPage ? "Loading…" : inbox.isFetchNextPageError ? "Retry" : "More"}
            </Button>
          )}
        </section>
      )}
    </div>
  )
}

function ConversationsSkeleton() {
  return (
    <LoadingSkeleton message="Loading inbox…" className="mt-1">
      <div className="hidden h-8.25 items-center gap-4 border-b @min-inbox-table/list:flex">
        {[0, 1, 2, 3].map((column) => <Skeleton key={column} className="h-2.5 w-3/25 first:w-2/25" />)}
      </div>
      {[0, 1].map((row) => (
        <div key={row} className="grid grid-cols-fill-fill-auto grid-rows-conversation-skeleton items-center gap-x-2 gap-y-1 border-b py-3.5 @min-inbox-table/list:h-row @min-inbox-table/list:grid-cols-conversation-skeleton @min-inbox-table/list:grid-rows-1 @min-inbox-table/list:gap-x-0 @min-inbox-table/list:py-0">
          <Skeleton className="h-2.5 w-12 max-w-17/20 @max-inbox-table/list:col-span-2 @max-inbox-table/list:row-start-3" />
          {[1, 2].map((person) => (
            <div key={person} className="flex min-w-0 items-center gap-2 @max-inbox-table/list:row-start-2 @min-inbox-table/list:pr-3">
              <Skeleton className="size-5 rounded-md" />
              <Skeleton className="h-3 min-w-0 max-w-1/2 flex-1" />
            </div>
          ))}
          <Skeleton className="h-3 w-36 max-w-full @max-inbox-table/list:col-span-2 @max-inbox-table/list:row-start-1" />
          <Skeleton className="hidden h-2.5 w-10 max-w-full @min-inbox-table/list:block" />
          <Skeleton className="h-2.5 w-7 max-w-full @max-inbox-table/list:row-start-1" />
          <Skeleton className="size-2 justify-self-end rounded-full @max-inbox-table/list:col-start-3 @max-inbox-table/list:row-start-2" />
        </div>
      ))}
    </LoadingSkeleton>
  )
}

function ThreadMessage({ item, replyCursor }: { item: ConversationItem; replyCursor?: number }) {
  const from = item.from
  const author = senderLabel(from)
  const channel = from?.channel_name || from?.channel
  const provenance = [
    from?.kind ? from.kind.replace(/_/g, " ") : null,
    from?.workspace,
    channel ? channelLabel(channel) : null,
    item.trust || from?.trust ? `Trust ${item.trust || from?.trust}` : null,
    from?.thread_id || from?.thread_ts ? `Thread ${displayReference(from.title || from.thread_id || from.thread_ts || "")}` : null,
    from?.message_id || item.event_id || from?.ts ? `Message ${from?.message_id || item.event_id || from?.ts}` : null,
  ].filter(Boolean)
  const kind = item.kind === "slack_event" ? item.slack_kind || "Slack event" : item.kind.replace(/_/g, " ")
  return (
    <li id={`message-${item.cursor}`} className="scroll-mt-4">
      <ListRow className="block space-y-1.5 px-0 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <Person label={author ? principalLabel(author) : kind} kind={senderLogoKind(from)} avatarUrl={from?.avatar_url} className="font-semibold" />
          <span className="font-mono text-mono-sm text-muted-foreground"><RelativeTime value={item.at} /></span>
        </div>
        {(provenance.length > 0 || item.in_reply_to || (author && kind !== "message") || item.status) && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-label-sm wrap-anywhere text-muted-foreground">
            {author && kind !== "message" && <span className="capitalize">{kind}</span>}
            {item.status && <span>Status {item.status.replace(/_/g, " ")}</span>}
            {provenance.map((part, index) => <span key={index}>{part}</span>)}
            {item.in_reply_to && (replyCursor
              ? <a className="underline underline-offset-2" href={`#message-${replyCursor}`}>In reply to {item.in_reply_to}</a>
              : <span>In reply to {item.in_reply_to}</span>)}
          </div>
        )}
        {item.text && <p className="wrap-anywhere whitespace-pre-wrap">{item.text}</p>}
      </ListRow>
    </li>
  )
}

function InboxConversation({ handle, search, onBack }: { handle: string; search: string; onBack: OpenConversation }) {
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const actions = useInboxActions()
  const thread = useInfiniteQuery({
    queryKey: ["conversation", handle],
    initialPageParam: 0,
    retry: false,
    queryFn: ({ pageParam, signal }) => {
      const params = new URLSearchParams({ cursor: String(pageParam), limit: String(itemLimit) })
      return requestJson(`/api/conversations/${encodeURIComponent(handle)}?${params}`, isConversationPage, { signal })
    },
    getNextPageParam: (page, _pages, cursor) => page.items.length === itemLimit && page.cursor > cursor ? page.cursor : undefined,
  })
  const conversation = thread.data?.pages[0]?.conversation
  const messages = thread.data?.pages.flatMap((page) => page.items) ?? []
  const people = conversation && participants(conversation)
  const lastItem = messages.reduce<ConversationItem | undefined>((last, item) => item.handle === conversation?.handle ? item : last, undefined)
  const receiptCursor = lastItem?.cursor
  const receiptCount = lastItem?.kind === "watch_digest" ? lastItem.count : undefined
  const root = conversation?.handle
  const csrf = session?.csrf
  useEffect(() => {
    if (!root || !receiptCursor || !csrf) return
    void markInboxRead(csrf, [root], { cursor: receiptCursor, count: receiptCount })
      .then(({ updated }) => markInboxCacheRead(client, updated), () => undefined)
  }, [client, csrf, receiptCount, receiptCursor, root])
  const updatedAt = conversation?.updated_at ?? conversation?.created_at
  const firstMessage = new Map<string, number>()
  for (const item of messages) {
    if (item.handle && !firstMessage.has(item.handle)) firstMessage.set(item.handle, item.cursor)
  }
  return (
    <div className="pt-2">
      <Link to={`/inbox${search}`} onClick={onBack} className="inline-flex items-center gap-2 text-body-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft aria-hidden className="size-4" strokeWidth={1.5} /> Conversations
      </Link>
      {thread.isPending && (
        <LoadingSkeleton message="Loading conversation…" className="mt-4">
          <div className="flex h-7 items-center"><Skeleton className="h-4 w-44 max-w-full" /></div>
          <div className="mt-2 flex h-5 items-center gap-3">
            {[0, 1].map((person) => (
              <div key={person} className="flex items-center gap-2">
                <Skeleton className="size-5 rounded-md" />
                <Skeleton className="h-3 w-12" />
              </div>
            ))}
          </div>
          <div className="mt-3 flex h-8.25 gap-5 border-b pt-0.5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-3 w-16" />
          </div>
          <ListRow className="block space-y-2 px-0 py-3.5">
            <div className="flex items-center gap-2">
              <Skeleton className="size-5 rounded-md" />
              <Skeleton className="h-3 w-24" />
              <Skeleton className="ml-auto h-2.5 w-8" />
            </div>
            <Skeleton className="h-2.5 w-24" />
            <Skeleton className="h-3 w-2/3" />
          </ListRow>
        </LoadingSkeleton>
      )}
      {thread.isError && !conversation && (
        <DataState kind="error" message="Conversation unavailable." onRetry={() => void thread.refetch()} />
      )}
      {conversation && people && (
        <section aria-labelledby="conversation-title" className="mt-4">
          <div className="flex items-start justify-between gap-2">
            <h2 id="conversation-title" className="text-page-title wrap-anywhere">{displayReference(conversation.title || "Message")}</h2>
            <RowActions row={conversation} actions={actions} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-body-sm">
            <Person label={people.from} kind={people.fromKind} avatarUrl={conversation.first_sender?.avatar_url} />
            <ArrowRight aria-label="to" className="size-4 text-muted-foreground" strokeWidth={1.5} />
            <Person label={people.to} kind={people.toKind} avatarUrl={conversation.target_avatar_url ?? undefined} />
          </div>
          <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-b pb-4 text-label-sm text-muted-foreground">
            <div className="flex flex-wrap gap-1"><dt>Project</dt><dd className="font-mono wrap-anywhere"><ProjectName project={conversation.project} /></dd></div>
            <div className="flex flex-wrap gap-1"><dt>Address</dt><dd className="font-mono wrap-anywhere">{conversation.target || conversation.destination}</dd></div>
            <div className="flex flex-wrap gap-1"><dt>Status</dt><dd className="capitalize">{conversation.status?.replaceAll("_", " ") || (conversation.open ? "Open" : "Closed")}</dd></div>
            {conversation.for_owner && <div><dt className="sr-only">Routing</dt><dd>For owner</dd></div>}
            <div className="flex flex-wrap gap-1"><dt>Conversation ID</dt><dd className="font-mono wrap-anywhere">{conversation.handle}</dd></div>
            {updatedAt && <div className="flex flex-wrap gap-1"><dt>Updated</dt><dd><RelativeTime value={updatedAt} /></dd></div>}
          </dl>
          {messages.length ? (
            <ol aria-label="Messages">
              {messages.map((item) => (
                <ThreadMessage key={item.cursor} item={item} replyCursor={firstMessage.get(item.in_reply_to ?? "")} />
              ))}
            </ol>
          ) : <DataState kind="empty" message="0 messages" />}
          {thread.isFetchNextPageError && (
            <DataState kind="error" message="Loading more messages failed." onRetry={() => void thread.fetchNextPage()} />
          )}
          {thread.hasNextPage && !thread.isFetchNextPageError && (
            <Button className="mt-4" variant="outline" size="sm" disabled={thread.isFetching} onClick={() => void thread.fetchNextPage()}>
              {thread.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          )}
        </section>
      )}
    </div>
  )
}

export function InboxPage() {
  const { handle } = useParams()
  const { search, state } = useLocation()
  const navigate = useNavigate()
  const list = useRef<HTMLDivElement>(null)
  const position = useRef<{ search: string; top: number }>(null)
  const previousHandle = useRef<string | undefined>(undefined)

  useLayoutEffect(() => {
    const mode = history.scrollRestoration
    history.scrollRestoration = "manual"
    return () => { history.scrollRestoration = mode }
  }, [])

  useLayoutEffect(() => {
    const previous = previousHandle.current
    previousHandle.current = handle
    if (handle === previous || !list.current || getComputedStyle(list.current).overflowY === "auto") return
    if (handle) window.scrollTo(0, 0)
    else if (position.current?.search === search) window.scrollTo(0, position.current.top)
  }, [handle, search])

  function open(event: MouseEvent<HTMLAnchorElement>) {
    if (!handle && primaryClick(event)) position.current = { search, top: window.scrollY }
  }

  function back(event: MouseEvent<HTMLAnchorElement>) {
    if (state?.fromList && primaryClick(event)) {
      event.preventDefault()
      void navigate(-1)
    }
  }

  return (
    <div className="@container">
      <div className="min-w-0 @min-inbox-split:grid @min-inbox-split:grid-cols-inbox-split">
        <div ref={list} className={cn("min-w-0 @container/list @min-inbox-split:block @min-inbox-split:max-h-inbox-pane @min-inbox-split:overflow-y-auto @min-inbox-split:pr-5",
          handle && "hidden")}>
          <InboxList active={handle} onOpen={open} />
        </div>
        <div className={cn("min-w-0 @min-inbox-split:block @min-inbox-split:max-h-inbox-pane @min-inbox-split:overflow-y-auto @min-inbox-split:border-l @min-inbox-split:pl-5",
          !handle && "hidden")}>
          {handle && <InboxConversation key={handle} handle={handle} search={search} onBack={back} />}
        </div>
      </div>
    </div>
  )
}
