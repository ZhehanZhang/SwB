import type { ReactNode } from "react"
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, ArrowRight, Check, Play, X } from "lucide-react"
import { Link, useParams } from "react-router-dom"
import { toast } from "sonner"
import { Lamp } from "@/components/console/lamp"
import { DataState } from "@/components/data-state"
import { ListRow } from "@/components/list-row"
import { Logo } from "@/components/logo"
import { Button } from "@/components/ui/button"
import { requestJson, sessionQuery } from "@/lib/api"
import { displayReference } from "@/lib/display-reference"
import { cn } from "@/lib/utils"
import {
  approvalTitle, channelLabel, isConversationPage, isInbox, isResumed, isWriteDecision, participants, principalLabel, senderLabel,
  type Conversation, type ConversationItem, type Pause, type PendingWrite,
} from "./inbox/data"

const inboxKey = ["inbox"] as const
const itemLimit = 100

function conversationLink(handle: string) {
  return `/inbox/${encodeURIComponent(handle)}`
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
  if (!time) return "—"
  return <time dateTime={time.at.toISOString()} title={time.at.toLocaleString()}>{time.short}</time>
}

function Person({ label, kind, className }: { label: string; kind?: string | null; className?: string }) {
  return (
    <span className={cn("flex min-w-0 items-center gap-2", className)}>
      <Logo kind={kind && kind !== "agent" ? kind : label} className="size-5" />
      <span className="min-w-0 wrap-anywhere">{label}</span>
    </span>
  )
}

function NeedsRow({ name, kind, title, at, preview, actions }: {
  name: string
  kind?: string
  title: ReactNode
  at: string | null
  preview?: ReactNode
  actions: ReactNode
}) {
  return (
    <li className="grid grid-cols-1 gap-3 border-b py-3.5 md:grid-cols-[minmax(0,1fr)_auto] md:items-center md:py-[15px]">
      <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-2.5 text-sm leading-[18px] md:grid-cols-[auto_auto_minmax(0,1fr)] md:gap-y-0.5 md:text-[13px]">
        <Logo kind={kind || name} className="col-start-1 row-start-1 size-[22px] md:row-span-2 md:size-7" />
        <span className="col-start-2 row-start-1 max-w-[14rem] min-w-0 font-semibold wrap-anywhere">{name}</span>
        <span className="col-start-3 row-start-1 font-mono text-xs leading-[18px] text-muted-foreground md:hidden"><RelativeTime value={at} /></span>
        <div className="col-span-3 row-start-2 min-w-0 wrap-anywhere md:col-span-1 md:col-start-3 md:row-start-1">
          {title}
          <span className="ml-2 hidden text-[12px] text-muted-foreground md:inline"><RelativeTime value={at} /></span>
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
      if (status === "failed") toast.error("Slack didn’t send the approved write.")
      else if (status === "rejected") toast.success("Denied Slack write.")
      else toast.success("Approved Slack write.")
    },
    onError: () => toast.error("Couldn’t decide the Slack request."),
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: inboxKey }),
      client.invalidateQueries({ queryKey: ["audit"] }),
    ]),
  })
  const disabled = !session?.csrf || decide.isPending || decide.isSuccess
  return (
    <NeedsRow
      name={principalLabel(item.key_id)}
      kind="slack"
      title={approvalTitle(item)}
      at={item.created_at}
      preview={item.text ? `“${item.text}”` : item.name || undefined}
      actions={(
        <>
          <Button
            size="sm" variant="ghost" className="min-h-9 border border-border-strong md:min-h-[26px] md:border-transparent"
            disabled={disabled} onClick={() => decide.mutate("reject")}
          >
            <X aria-hidden className="max-md:hidden" />{decide.isPending && decide.variables === "reject" ? "Denying…" : "Deny"}
          </Button>
          <Button
            size="sm" className="min-h-9 md:min-h-[26px]" disabled={disabled} onClick={() => decide.mutate("approve")}
          >
            <Check aria-hidden />{decide.isPending && decide.variables === "approve" ? "Approving…" : "Approve"}
          </Button>
        </>
      )}
    />
  )
}

function TurnPause({ pause, conversation }: { pause: Pause; conversation?: Conversation }) {
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const resume = useMutation({
    mutationFn: () => requestJson(`/api/inbox/pauses/${encodeURIComponent(pause.root)}/resume`, isResumed, {
      method: "POST", csrf: session?.csrf,
    }),
    onSuccess: () => toast.success("Resumed conversation."),
    onError: () => toast.error("Couldn’t resume the conversation."),
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: inboxKey }),
      client.invalidateQueries({ queryKey: ["conversation", pause.root] }),
      client.invalidateQueries({ queryKey: ["audit"] }),
    ]),
  })
  return (
    <NeedsRow
      name={displayReference(conversation?.title || pause.root)}
      kind={conversation?.destination}
      title={<Link className="hover:underline" to={conversationLink(pause.root)}>Paused at the turn cap</Link>}
      at={pause.paused_at}
      preview={`${pause.turns} turns`}
      actions={(
        <Button
          size="sm" className="min-h-9 md:min-h-[26px]" disabled={!session?.csrf || resume.isPending || resume.isSuccess}
          onClick={() => resume.mutate()}
        >
          <Play aria-hidden />{resume.isPending ? "Resuming…" : "Resume"}
        </Button>
      )}
    />
  )
}

function Conversations({ items }: { items: Conversation[] }) {
  const conversations = [...items].sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? ""))
  return (
    <table role="table" className="mt-1 w-full text-left max-md:block md:table-fixed">
      <caption className="sr-only">Conversations</caption>
      <thead role="rowgroup" className="sr-only md:not-sr-only">
        <tr role="row" className="border-b text-[11px] leading-4 text-muted-foreground">
          <th role="columnheader" className="w-[9.75%] py-2 pr-3 font-medium" scope="col">Project</th>
          <th role="columnheader" className="w-[16%] py-2 pr-3 font-medium" scope="col">From</th>
          <th role="columnheader" className="w-[16%] py-2 pr-3 font-medium" scope="col">To</th>
          <th role="columnheader" className="py-2 pr-3 font-medium" scope="col">Subject</th>
          <th role="columnheader" className="w-[9.25%] py-2 pr-3 font-medium" scope="col">Updated</th>
          <th role="columnheader" className="w-4" scope="col"><span className="sr-only">Open</span></th>
        </tr>
      </thead>
      <tbody role="rowgroup" className="max-md:block">
        {conversations.map((row) => {
          const { from, to, fromKind, toKind } = participants(row)
          return (
            <tr key={row.handle} role="row" className="relative border-b text-[13px] leading-[18px] hover:bg-muted/40 focus-within:bg-muted/40 max-md:grid max-md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] max-md:gap-x-2 max-md:gap-y-1 max-md:py-3.5 md:h-[43px]">
              <td role="cell" className="font-mono text-xs wrap-anywhere max-md:col-span-2 max-md:row-start-3 md:py-2 md:pr-3">{row.project ?? "—"}</td>
              <td role="cell" className="max-md:col-start-1 max-md:row-start-2 md:py-2 md:pr-3"><Person label={from} kind={fromKind} /></td>
              <td role="cell" className="max-md:col-start-2 max-md:row-start-2 md:py-2 md:pr-3"><Person label={to} kind={toKind} /></td>
              <td role="cell" className="wrap-anywhere max-md:col-span-2 max-md:row-start-1 max-md:font-semibold md:py-2 md:pr-3">
                <Link to={conversationLink(row.handle)} className="after:absolute after:inset-0 focus-visible:after:outline-2 focus-visible:after:outline-ring">
                  {displayReference(row.title || row.handle)}
                </Link>
              </td>
              <td role="cell" className="font-mono text-mono-sm text-muted-foreground max-md:col-start-3 max-md:row-start-1 md:py-2 md:pr-2">
                <RelativeTime value={row.updated_at ?? row.created_at} />
              </td>
              <td role="cell" className="text-right max-md:col-start-3 max-md:row-start-2">
                <Lamp state={row.open ? "on" : "off"} label={row.open ? "Open" : "Closed"} />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function InboxList() {
  const inbox = useQuery({
    queryKey: inboxKey,
    queryFn: ({ signal }) => requestJson("/api/inbox", isInbox, { signal }),
    refetchInterval: 30_000,
  })
  const data = inbox.data
  return (
    <div className="space-y-8 pt-2 md:space-y-[34px]">
      <section aria-labelledby="needs-you">
        <h2 id="needs-you" className="text-heading-md font-semibold tracking-[-0.01em]">Needs you</h2>
        {inbox.isPending && <DataState kind="loading" message="Loading inbox…" />}
        {inbox.isError && <DataState kind="error" message="Couldn’t load the inbox." onRetry={() => void inbox.refetch()} />}
        {data && (data.pending_writes.length || data.pauses.length ? (
          <ul className="mt-2">
            {data.pending_writes.map((item) => <SlackApproval key={item.id} item={item} />)}
            {data.pauses.map((pause) => (
              <TurnPause key={pause.root} pause={pause} conversation={data.conversations.find((row) => row.handle === pause.root)} />
            ))}
          </ul>
        ) : <DataState kind="empty" message="Nothing needs you." />)}
      </section>
      {data && (
        <section aria-labelledby="conversations">
          <h2 id="conversations" className="text-heading-md font-semibold tracking-[-0.01em]">Conversations</h2>
          {data.conversations.length
            ? <Conversations items={data.conversations} />
            : <DataState kind="empty" message="No conversations yet." />}
        </section>
      )}
    </div>
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
    item.trust || from?.trust ? `Trust: ${item.trust || from?.trust}` : null,
    from?.thread_id || from?.thread_ts ? `Thread: ${displayReference(from.title || from.thread_id || from.thread_ts || "")}` : null,
    from?.message_id || item.event_id || from?.ts ? `Message: ${from?.message_id || item.event_id || from?.ts}` : null,
  ].filter(Boolean)
  const kind = item.kind === "slack_event" ? item.slack_kind || "Slack event" : item.kind.replace(/_/g, " ")
  return (
    <li id={`message-${item.cursor}`} className="scroll-mt-4">
      <ListRow className="block space-y-1.5 px-0 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <Person label={author ? principalLabel(author) : kind} kind={from?.kind} className="font-semibold" />
          <span className="font-mono text-mono-sm text-muted-foreground"><RelativeTime value={item.at} /></span>
        </div>
        {(provenance.length > 0 || item.in_reply_to || (author && kind !== "message") || item.status) && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs wrap-anywhere text-muted-foreground">
            {author && kind !== "message" && <span className="capitalize">{kind}</span>}
            {item.status && <span>Status: {item.status.replace(/_/g, " ")}</span>}
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

function InboxConversation({ handle }: { handle: string }) {
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
  const firstMessage = new Map<string, number>()
  for (const item of messages) {
    if (item.handle && !firstMessage.has(item.handle)) firstMessage.set(item.handle, item.cursor)
  }
  return (
    <div className="pt-2">
      <Link to="/inbox" className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft aria-hidden className="size-4" strokeWidth={1.5} /> Conversations
      </Link>
      {thread.isPending && <DataState kind="loading" message="Loading conversation…" />}
      {thread.isError && !conversation && (
        <DataState kind="error" message="Couldn’t load this conversation." onRetry={() => void thread.refetch()} />
      )}
      {conversation && people && (
        <section aria-labelledby="conversation-title" className="mt-4">
          <h2 id="conversation-title" className="text-page-title wrap-anywhere">{displayReference(conversation.title || conversation.handle)}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <Person label={people.from} kind={people.fromKind} />
            <ArrowRight aria-label="to" className="size-4 text-muted-foreground" strokeWidth={1.5} />
            <Person label={people.to} kind={people.toKind} />
          </div>
          <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-b pb-4 text-xs text-muted-foreground">
            {conversation.project && <div className="flex flex-wrap gap-1"><dt>Project</dt><dd className="font-mono wrap-anywhere">{conversation.project}</dd></div>}
            <div className="flex flex-wrap gap-1"><dt>Via</dt><dd className="font-mono wrap-anywhere">{conversation.destination}</dd></div>
            <div className="flex flex-wrap gap-1"><dt>Status</dt><dd>{conversation.status?.replace(/_/g, " ") || (conversation.open ? "Open" : "Closed")}</dd></div>
            <div className="flex flex-wrap gap-1"><dt>Handle</dt><dd className="font-mono wrap-anywhere">{conversation.handle}</dd></div>
            <div className="flex flex-wrap gap-1"><dt>Updated</dt><dd><RelativeTime value={conversation.updated_at ?? conversation.created_at} /></dd></div>
          </dl>
          {messages.length ? (
            <ol aria-label="Messages">
              {messages.map((item) => (
                <ThreadMessage key={item.cursor} item={item} replyCursor={firstMessage.get(item.in_reply_to ?? "")} />
              ))}
            </ol>
          ) : <DataState kind="empty" message="No messages yet." />}
          {thread.isFetchNextPageError && (
            <DataState kind="error" message="Couldn’t load more messages." onRetry={() => void thread.fetchNextPage()} />
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
  return handle ? <InboxConversation key={handle} handle={handle} /> : <InboxList />
}
