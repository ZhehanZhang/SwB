import { useId, useState } from "react"
import { ChevronDown } from "lucide-react"
import { Link } from "react-router-dom"
import { LogoTile } from "@/components/logo-tile"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { agentLogo } from "@/lib/agent-identity"
import { cn } from "@/lib/utils"
import type { Mode, ModeChanges, ModeRow, ModesStatus, ModeTarget, TriageAction } from "@/lib/channel-modes"

const modeLabel = { off: "Off", watch: "Watch", assist: "Assist", triage: "Triage", work: "Work", inherit: "Inherit" }
const sources = { override: "Override", channel: "From channel", space: "From space", project: "From project", default: "Default", legacy: "From earlier settings" }
const actionLabel = { reply: "Reply", label: "Label", comment: "Comment", open_issue: "Open issue", link_duplicate: "Link duplicate", notify_owner: "Notify owner" }
const issueActions = new Set<TriageAction>(["label", "comment", "open_issue", "link_duplicate"])
const menuButton = cn("min-h-8 max-w-full justify-between gap-2 px-2 [&_svg]:size-3.5")
const settingsUrl = /^https:\/\/github\.com\/(?:organizations\/[\w-]+\/)?settings\/installations\/\d+$/
const reportDate = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })

export function rowName(row: ModeRow) {
  return row.name ?? (row.channel || row.space)
}

export function modeOptions(row: ModeRow, status: ModesStatus) {
  return row.kind === "dm" ? status.schema.dm_modes : status.schema.modes
}

export function ModeMenu({ label, value, options, reset, disabled, onChange }: {
  label: string; value?: ModeChanges["mode"]; options: Mode[]; reset: boolean; disabled: boolean;
  onChange: (value: ModeChanges["mode"]) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={menuButton} aria-label={label} disabled={disabled}>
          {value ? modeLabel[value] : "Mode"} <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup value={value ?? ""} onValueChange={next => {
          if (next === "inherit" || options.includes(next as Mode)) onChange(next as ModeChanges["mode"])
        }}>
          {options.map(mode => <DropdownMenuRadioItem key={mode} value={mode}>{modeLabel[mode]}</DropdownMenuRadioItem>)}
          {reset && <><DropdownMenuSeparator /><DropdownMenuRadioItem value="inherit">Reset to inherit</DropdownMenuRadioItem></>}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function TargetIdentity({ target }: { target: ModeTarget }) {
  return (
    <>
      {target.kind === "captain"
        ? <LogoTile kind="captain" avatarUrl={target.console_avatar_url} className="size-[18px]" />
        : <LogoTile kind={target.kind === "agent" ? agentLogo(target.id) : target.kind}
          avatarUrl={target.console_avatar_url} className="size-[18px]" />}
      <span className="min-w-0 truncate">{target.name}</span>
    </>
  )
}

export function TargetMenu({ label, address, targets, unavailable, busy, bulk, disabled, onChange }: {
  label: string; address?: string | null; targets: ModeTarget[]; unavailable: boolean; busy: boolean; bulk?: boolean;
  disabled: boolean; onChange: (address: string) => void
}) {
  const key = address?.toLowerCase()
  const active = key ? targets.find(target => target.address.toLowerCase() === key || target.slugs.includes(key)) : null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={menuButton} aria-label={label} title={address || undefined} disabled={disabled}>
          {active ? <TargetIdentity target={active} />
            : <span className="truncate">{bulk ? "Target" : address === "" ? "Inherit target" : address ? "Agent unavailable" : "Choose target"}</span>}
          <ChevronDown aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-w-[min(288px,calc(100vw-2rem))]">
        <DropdownMenuRadioGroup value={active?.address ?? (address === "" ? "" : undefined)}
          onValueChange={onChange}>
          <DropdownMenuRadioItem value="">Inherit target</DropdownMenuRadioItem>
          {targets.map(target => (
            <DropdownMenuRadioItem key={target.address} value={target.address} title={target.address} textValue={target.name}>
              <TargetIdentity target={target} />
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        {(busy || unavailable || targets.length === 0) && (
          <div className="px-2.5 py-2 text-caption text-muted-foreground" role="status">
            {unavailable ? "Targets unavailable" : busy ? "Loading targets…" : "Available targets 0"}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function issuePermissions(row: ModeRow, status: ModesStatus) {
  const repos = row.kind === "repo" && row.channel ? [row.channel] : status.spaces
    .filter(entry => entry.kind === "repo" && row.project && entry.project === row.project && entry.channel)
    .map(entry => entry.channel)
  const installations = status.github?.installations ?? []
  const matching = installations.filter(entry => (!entry.block || row.kind !== "repo" || row.block === entry.block)
    && entry.repositories.some(repo => repos.includes(repo)))
  const canWrite = Boolean((row.kind === "repo" || row.project) && (installations.length
    ? matching.some(entry => entry.permissions.issues === "write")
    : status.github?.installation?.issues === "write"))
  const urls = [...new Set(matching.map(entry => entry.permissions_url).filter(url => url && settingsUrl.test(url)))]
  return { canWrite, url: urls.length === 1 ? urls[0] : null }
}

export function ChannelModeRow({ row, status, targets, targetsUnavailable, targetsBusy, draft, error, selected, busy, onSelect, onChange }: {
  row: ModeRow; status: ModesStatus; targets: ModeTarget[]; targetsUnavailable: boolean; targetsBusy: boolean;
  draft?: Partial<ModeChanges>; error?: string; selected: boolean; busy: boolean;
  onSelect: () => void; onChange: (changes: Partial<ModeChanges>) => void
}) {
  const detailId = useId()
  const [open, setOpen] = useState(false)
  const name = rowName(row)
  const mode = draft?.mode ?? row.mode
  const inherited = mode === "inherit"
  const target = draft?.target ?? row.target
  const actions = draft?.actions ?? row.triage_actions ?? (row.mode === "triage" ? row.actions : ["reply"])
  const space = row.space === name || row.space === row.channel ? null : row.space
  const issue = issuePermissions(row, status)
  const rowCell = cn("self-center py-1 md:py-2 md:pr-3")
  return (
    <>
      <tr className="border-b max-md:grid max-md:grid-cols-[auto_minmax(0,1fr)_auto] max-md:gap-x-2 max-md:py-2">
        <td className={rowCell}>
          <input type="checkbox" aria-label={`Select ${name}`} checked={selected} onChange={onSelect} disabled={busy}
            className="size-4 align-middle accent-primary" />
        </td>
        <td className={rowCell}>
          <div className="flex min-w-0 items-center gap-2">
            <LogoTile kind={row.kind === "repo" ? "github" : row.block} className="size-5" />
            <span className="min-w-0 wrap-anywhere text-label-sm">{name}</span>
          </div>
          {space && <div className="ps-7 text-caption wrap-anywhere text-muted-foreground">{space}</div>}
        </td>
        <td className={rowCell}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <ModeMenu label={`Mode for ${name}`} value={mode} options={modeOptions(row, status)}
              reset={row.override_channel != null || !!draft} disabled={busy} onChange={value => onChange({ mode: value })} />
            {!draft?.mode && <span className="text-caption text-muted-foreground">{sources[row.source]}</span>}
          </div>
        </td>
        <td className={`${rowCell} max-md:col-start-2`}>
          <TargetMenu label={`Target for ${name}`} address={target} targets={targets}
            unavailable={targetsUnavailable} busy={targetsBusy} disabled={busy || inherited}
            onChange={address => onChange({ target: address })} />
        </td>
        <td className={`${rowCell} text-right`}>
          <Button variant="ghost" size="sm" className="min-h-8 px-2 text-muted-foreground hover:text-foreground"
            aria-label={`Details for ${name}`} aria-expanded={open} aria-controls={detailId} onClick={() => setOpen(!open)}>
            Details <ChevronDown aria-hidden className={open ? "rotate-180" : undefined} />
          </Button>
        </td>
      </tr>
      {error && <tr className="border-b max-md:block"><td colSpan={5} className="pb-2 ps-7"><p role="alert" className="text-destructive">{error}</p></td></tr>}
      <tr id={detailId} hidden={!open} className="border-b max-md:[&:not([hidden])]:block">
        <td colSpan={5} className="space-y-3 py-3 ps-7 max-md:block">
          {row.digest && (
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-caption">
              <div className="flex gap-1"><dt className="text-muted-foreground">Pending messages</dt><dd>{row.digest.pending}</dd></div>
              {row.digest.next_at !== null && <div className="flex gap-1"><dt className="text-muted-foreground">Next report</dt>
                <dd>{reportDate.format(row.digest.next_at * 1000)}</dd></div>}
              <div className="flex gap-1"><dt className="text-muted-foreground">Destination</dt><dd><Link to="/inbox" className="underline">Inbox</Link></dd></div>
            </dl>
          )}
          {status.schema.actions.length > 0 && row.kind !== "dm" && (
            <fieldset>
              <legend className="text-label-sm">Triage actions</legend>
              <div className="mt-1 grid max-w-[680px] gap-x-6 gap-y-0.5 sm:grid-cols-2 xl:grid-cols-3">
                {status.schema.actions.map(action => (
                  <label key={action} className="flex min-h-9 items-center justify-between gap-2 border-b py-1.5 has-disabled:text-muted-foreground">
                    <span>{actionLabel[action]}</span>
                    <input role="switch" type="checkbox" checked={actions.includes(action)} className="peer sr-only"
                      disabled={busy || mode !== "triage" || (issueActions.has(action) && !issue.canWrite)}
                      onChange={event => onChange({ actions: event.currentTarget.checked
                        ? [...actions, action] : actions.filter(value => value !== action) })} />
                    <span aria-hidden className="relative h-[18px] w-8 shrink-0 rounded-full border border-border-strong bg-muted before:absolute before:top-0.5 before:left-0.5 before:size-3 before:rounded-full before:bg-muted-foreground peer-checked:bg-foreground peer-checked:before:translate-x-3.5 peer-checked:before:bg-background peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ring peer-disabled:opacity-45" />
                  </label>
                ))}
              </div>
              {!issue.canWrite && issue.url && <a href={issue.url} target="_blank" rel="noreferrer"
                className="mt-2 inline-block underline underline-offset-2">Review permissions</a>}
            </fieldset>
          )}
        </td>
      </tr>
    </>
  )
}
