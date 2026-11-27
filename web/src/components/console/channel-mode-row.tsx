import { useId, useState, type ReactNode } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown } from "lucide-react"
import { Link } from "react-router-dom"
import { Button } from "@/components/ui/button"
import { sessionQuery } from "@/lib/api"
import { channelModesQuery, modeKey, saveChannelMode, type Mode, type ModeRow, type ModesStatus, type TriageAction } from "@/lib/channel-modes"

const modeLabel = { off: "Off", watch: "Watch", assist: "Assist", triage: "Triage", work: "Work", inherit: "Inherit" }
const sourceLabel = {
  override: "Override", channel: "From channel", space: "From space", project: "From project", default: "Default", legacy: "From earlier settings",
}
const actionLabel = { reply: "Reply", label: "Label", comment: "Comment", open_issue: "Open issue", link_duplicate: "Link duplicate", notify_owner: "Notify owner" }
const issueActions = new Set<TriageAction>(["label", "comment", "open_issue", "link_duplicate"])
const problemWords = /\b(?:no|not|never|without|nothing|none|\w+n[’']t)\b|[:;—]/i

function problemCopy(problem: string) {
  if (/privacy mode.*mention/i.test(problem)) return "Telegram shares mentions in privacy mode."
  if (/github credential/i.test(problem)) return "GitHub actions require a credential."
  if (/permission|access|installation|\bissues\b.*write/i.test(problem)) return "GitHub App permissions need review."
  return problemWords.test(problem) || problem.trim().split(/\s+/).length > 20 ? "Check the connection settings." : problem
}

function displayDate(value: number) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(value * 1000)
}

export function ChannelModeRow({ row, status, label, mapping, children, columns }: {
  row: ModeRow; status: ModesStatus; label: ReactNode; mapping: string | null; children: ReactNode; columns: number
}) {
  const detailId = useId()
  const [open, setOpen] = useState(false)
  const { data: session } = useQuery(sessionQuery)
  const client = useQueryClient()
  const update = useMutation({
    mutationFn: ({ mode, actions }: { mode: Mode | "inherit"; actions?: TriageAction[] }) =>
      saveChannelMode(row, mode, actions, session?.csrf ?? null),
    onSuccess: saved => {
      client.setQueryData(channelModesQuery.queryKey, old => old && ({
        ...old, spaces: old.spaces.map(entry => modeKey(entry) === modeKey(saved) ? saved : entry),
      }))
      return client.invalidateQueries({ queryKey: channelModesQuery.queryKey })
    },
  })
  const choices = row.kind === "dm" ? status.schema.dm_modes : status.schema.modes
  const name = row.name ?? (row.channel || row.space)
  const disabled = !session?.csrf || update.isPending
  const repoBlocked = row.kind === "repo" && row.problems.some(problem => /github|installation|permission|access/i.test(problem))
  const canWriteIssues = Boolean(row.project && !repoBlocked && status.github?.installation?.issues === "write")
  const problems = [...new Set(row.problems.map(problemCopy))]
  const disclosure = {
    "aria-label": `Details for ${name}`,
    "aria-expanded": open,
    "aria-controls": detailId,
    onClick: () => setOpen(!open),
  }
  const select = (
    <select aria-label={`Mode for ${name}`} value={row.mode} disabled={disabled}
      onChange={event => {
        const mode = event.currentTarget.value
        if (mode === "inherit" || choices.some(choice => choice === mode)) update.mutate({ mode: mode as Mode | "inherit" })
      }}
      className="min-h-10 max-w-full rounded-sm border border-border-strong bg-surface px-1.5 text-body-sm disabled:opacity-60 md:min-h-8">
      {choices.map(mode => <option key={mode} value={mode}>{modeLabel[mode]}</option>)}
      <option value="inherit">{modeLabel.inherit}</option>
    </select>
  )
  const error = update.isError && <div role="alert" className="mt-1 text-caption text-destructive">Mode update failed</div>
  return (
    <>
      <tr className="border-b">
        {children}
        <td className="hidden py-2 pe-2 align-top md:table-cell">
          <div className="flex flex-wrap items-center gap-x-1">
            {select}
            <Button variant="ghost" size="icon" className="size-7" {...disclosure}>
              <ChevronDown aria-hidden className={open ? "rotate-180" : undefined} />
            </Button>
          </div>
          <div className="text-caption text-muted-foreground">{sourceLabel[row.source]}</div>
          {error}
        </td>
        <td colSpan={columns} className="p-0 md:hidden">
          <div className="relative isolate grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-2 gap-y-0.5 py-2">
            <button type="button" className="absolute inset-0 cursor-pointer rounded-sm focus-visible:ring-2 focus-visible:ring-ring"
              {...disclosure} />
            <div className="pointer-events-none relative min-w-0 truncate text-label-sm">{label}</div>
            <div className="relative z-10">{select}</div>
            <ChevronDown aria-hidden className={`pointer-events-none relative size-4 text-muted-foreground ${open ? "rotate-180" : ""}`} />
            <div className="pointer-events-none relative col-span-3 flex min-w-0 items-baseline gap-x-3 text-caption text-muted-foreground">
              {mapping && <span className="min-w-0 truncate font-mono">{mapping}</span>}
              <span className="shrink-0">{sourceLabel[row.source]}</span>
            </div>
          </div>
          {error}
        </td>
      </tr>
      <tr id={detailId} hidden={!open} className="border-b">
        <td colSpan={columns} className="space-y-4 py-3">
          {(row.target || row.digest) && (
            <dl className="grid gap-x-5 gap-y-1.5 sm:grid-cols-[auto_1fr]">
              {row.target && <><dt className="text-muted-foreground">Agent</dt><dd className="font-mono">{row.target}</dd></>}
              {row.digest && (
                <>
                  <dt className="text-muted-foreground">Pending messages</dt><dd>{row.digest.pending}</dd>
                  {row.digest.next_at !== null && <><dt className="text-muted-foreground">Next report</dt><dd className="font-mono">{displayDate(row.digest.next_at)}</dd></>}
                  <dt className="text-muted-foreground">Destination</dt><dd><Link to="/inbox" className="underline">Inbox</Link></dd>
                </>
              )}
            </dl>
          )}
          {status.schema.actions.length > 0 && row.kind !== "dm" && (
            <fieldset className="space-y-1.5">
              <legend className="text-label-sm">Triage actions</legend>
              {status.schema.actions.map(action => {
                const unavailable = issueActions.has(action) && !canWriteIssues
                return (
                  <label key={action} className="flex flex-wrap items-center gap-x-2">
                    <input type="checkbox" checked={row.actions.includes(action)} disabled={disabled || row.mode !== "triage" || unavailable}
                      onChange={event => update.mutate({ mode: "triage", actions: event.currentTarget.checked
                        ? [...row.actions, action] : row.actions.filter(value => value !== action) })} />
                    <span className={unavailable ? "text-muted-foreground" : undefined}>{actionLabel[action]}</span>
                  </label>
                )
              })}
              {!canWriteIssues && status.schema.actions.some(action => issueActions.has(action)) && (
                <p className="text-caption text-muted-foreground">Issue actions require GitHub write access.</p>
              )}
            </fieldset>
          )}
          {problems.map(problem => <p key={problem} className="text-body-sm text-muted-foreground">{problem}</p>)}
        </td>
      </tr>
    </>
  )
}
