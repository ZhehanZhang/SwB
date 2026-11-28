import { useState, type FormEvent } from "react"
import { Search, SlidersHorizontal } from "lucide-react"
import { Popover } from "radix-ui"
import { useSearchParams } from "react-router-dom"
import { Button } from "@/components/ui/button"

const textFilters = [["sender", "Sender"], ["recipient", "Recipient"], ["block", "Block"], ["channel", "Channel"], ["project", "Project"]] as const
const valueFilters = [
  ["kind", "Kind", {
    message: "Message", reply: "Reply", final: "Result", watch_digest: "Watch digest", triage_note: "Triage note",
    slack_event: "Slack event", telegram_event: "Telegram event", discord_event: "Discord event", context: "Context", conversation: "Conversation",
  }],
  ["status", "Status", {
    sent: "Sent", delivered: "Delivered", answered: "Answered", completed: "Completed", cancelled: "Cancelled",
  }],
  ["unread", "Read state", { 1: "Unread", 0: "Read" }],
] as const
const fields = [...textFilters, ...valueFilters].map(([key]) => key)
const queryFields = ["q", "sort", ...fields]
const control = "min-h-9 min-w-0 rounded-md border border-border-strong bg-surface px-2.5 text-sm"

export function inboxQuery(params: URLSearchParams) {
  const query = new URLSearchParams()
  for (const key of queryFields) {
    const value = params.get(key)
    if (value) query.set(key, value)
  }
  return query
}

export function InboxFilters() {
  const [params, setParams] = useSearchParams()
  const [open, setOpen] = useState(false)
  const active = fields.some((key) => params.has(key))
  const search = params.get("q") || ""

  function update(values: FormData | URLSearchParams, keys: readonly string[]) {
    const next = new URLSearchParams(params)
    for (const key of keys) {
      const value = values.get(key)?.toString().trim()
      if (value) next.set(key, value)
      else next.delete(key)
    }
    if (next.toString() !== params.toString()) setParams(next)
  }

  function submit(event: FormEvent<HTMLFormElement>, keys: readonly string[]) {
    event.preventDefault()
    update(new FormData(event.currentTarget), keys)
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <form className="flex min-w-40 flex-1 items-center gap-1" onSubmit={(event) => submit(event, ["q"])}>
        <input type="search" name="q" aria-label="Search inbox" placeholder="Search inbox" className={`${control} w-full`}
          key={search} defaultValue={search} maxLength={500} />
        <Button type="submit" variant="ghost" size="icon" aria-label="Search"><Search aria-hidden /></Button>
      </form>
      <select aria-label="Sort conversations" className={control} value={params.get("sort") === "oldest" ? "oldest" : ""}
        onChange={(event) => update(new URLSearchParams({ sort: event.currentTarget.value }), ["sort"])}>
        <option value="">Newest first</option>
        <option value="oldest">Oldest first</option>
      </select>
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger asChild>
          <Button variant={active ? "secondary" : "outline"} size="sm" className="min-h-9">
            <SlidersHorizontal aria-hidden /> Filters
          </Button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content align="end" sideOffset={6} aria-label="Inbox filters"
            className="z-50 max-h-[65dvh] w-[min(22rem,calc(100vw-2rem))] overflow-y-auto rounded-md border border-border-strong bg-surface p-3 shadow-md">
            <form key={params.toString()} className="grid grid-cols-2 gap-2" onSubmit={(event) => {
              submit(event, fields)
              setOpen(false)
            }}>
              {textFilters.map(([key, label]) => (
                <label key={key} className="grid min-w-0 gap-1 text-xs">
                  {label}
                  <input name={key} className={control} defaultValue={params.get(key) || ""} maxLength={key === "project" ? 48 : 200} />
                </label>
              ))}
              {valueFilters.map(([key, label, choices]) => (
                <label key={key} className="grid min-w-0 gap-1 text-xs">
                  {label}
                  <select name={key} className={control} defaultValue={params.get(key) || ""}>
                    <option value="">Any</option>
                    {Object.entries(choices).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
                  </select>
                </label>
              ))}
              <div className="col-span-2 mt-1 flex justify-end gap-2">
                <Popover.Close asChild>
                  <Button variant="ghost" onClick={() => update(new URLSearchParams(), fields)}>Clear</Button>
                </Popover.Close>
                <Button type="submit">Apply</Button>
              </div>
            </form>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
    </div>
  )
}
