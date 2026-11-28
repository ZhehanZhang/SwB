import { useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChannelModeRow, ModeMenu, TargetMenu, modeOptions } from "@/components/console/channel-mode-row"
import { Button } from "@/components/ui/button"
import { ApiError, sessionQuery } from "@/lib/api"
import { channelModesQuery, modeKey, modeTargetsQuery, saveChannelMode, type ModeChanges, type ModeRow, type ModesStatus } from "@/lib/channel-modes"

type Draft = Partial<ModeChanges>

function saveError(error: unknown) {
  if (error instanceof ApiError) {
    if (error.code === "csrf") return "Refresh this page and try again."
    if (error.code === "mode_not_saved") return "Check this channel’s mode and target, then try again."
  }
  return "Save failed. Try again."
}

export function ChannelModeTable({ label, rows, status }: { label: string; rows: ModeRow[]; status: ModesStatus }) {
  const [selection, setSelection] = useState<string[]>([])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const client = useQueryClient()
  const { data: session } = useQuery(sessionQuery)
  const targets = useQuery({ ...modeTargetsQuery, enabled: rows.length > 0 })
  const targetList = targets.data ?? []
  const selected = rows.filter(row => selection.includes(modeKey(row)))
  const changed = rows.filter(row => drafts[modeKey(row)])
  const selectionOptions = status.schema.modes.filter(mode => selected.every(row => modeOptions(row, status).includes(mode)))
  const disabled = !session?.csrf || saving
  const allSelected = selected.length === rows.length
  if (rows.length === 0) return null

  function stage(entries: ModeRow[], patch: Draft) {
    const keys = entries.map(modeKey)
    setErrors(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keys.includes(key))))
    setDrafts(old => {
      const next = { ...old }
      for (const row of entries) {
        const key = modeKey(row)
        if (patch.mode === "inherit") {
          if (row.override_channel != null) next[key] = { mode: "inherit" }
          else delete next[key]
          continue
        }
        const draft = { ...next[key], ...patch }
        if (draft.mode === row.mode) delete draft.mode
        if (draft.target === row.target) delete draft.target
        const triage = row.triage_actions ?? row.actions
        if (draft.actions && draft.actions.length === triage.length
          && draft.actions.every(action => triage.includes(action))) delete draft.actions
        if (Object.keys(draft).length > 0) next[key] = draft
        else delete next[key]
      }
      return next
    })
  }

  async function save() {
    setSaving(true)
    try {
      const results = await Promise.allSettled(changed.map(row => {
        const draft = drafts[modeKey(row)]
        return saveChannelMode(row, { ...draft, mode: draft.mode ?? row.mode }, session?.csrf ?? null)
      }))
      const failed = Object.fromEntries(results.flatMap((result, i) =>
        result.status === "rejected" ? [[modeKey(changed[i]), saveError(result.reason)]] : []))
      client.setQueryData(channelModesQuery.queryKey, old => old && ({
        ...old, spaces: old.spaces.map(row => {
          const result = results[changed.findIndex(entry => modeKey(entry) === modeKey(row))]
          return result?.status === "fulfilled" && modeKey(result.value) === modeKey(row) ? result.value : row
        }),
      }))
      await client.invalidateQueries({ queryKey: channelModesQuery.queryKey })
      setErrors(failed)
      setDrafts(old => Object.fromEntries(Object.entries(old).filter(([key]) => Object.hasOwn(failed, key))))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2">
      {(selected.length > 0 || changed.length > 0) && (
        <div role="group" aria-label="Channel mode editing"
          className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-y py-2 text-body-sm">
          <div className="flex flex-wrap items-center gap-2">
            {selected.length > 0 && (
              <>
                <span className="text-muted-foreground" aria-live="polite">{selected.length} selected</span>
                <ModeMenu label="Mode for selected channels" options={selectionOptions}
                  reset={selected.some(row => row.override_channel != null || drafts[modeKey(row)])}
                  disabled={disabled} onChange={mode => stage(selected, { mode })} />
                <TargetMenu label="Target for selected channels" targets={targetList} unavailable={targets.isError}
                  busy={targets.isPending} bulk disabled={disabled || selected.every(row => drafts[modeKey(row)]?.mode === "inherit")}
                  onChange={target => stage(selected.filter(row => drafts[modeKey(row)]?.mode !== "inherit"), { target })} />
                <Button variant="ghost" size="sm" disabled={saving} onClick={() => setSelection([])}>Clear selection</Button>
              </>
            )}
          </div>
          {changed.length > 0 && (
            <div className="ms-auto flex items-center gap-2">
              <span className="text-muted-foreground" aria-live="polite">{changed.length} {changed.length === 1 ? "change" : "changes"}</span>
              <Button variant="ghost" size="sm" disabled={saving} onClick={() => { setDrafts({}); setErrors({}) }}>Cancel</Button>
              <Button size="sm" disabled={disabled} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</Button>
            </div>
          )}
        </div>
      )}
      <table aria-label={label} className="w-full table-fixed border-collapse text-left text-body-sm max-md:block">
        <thead>
          <tr className="border-b text-caption text-muted-foreground max-md:block max-md:py-2">
            <th scope="col" className="w-8 py-2 font-normal">
              <input type="checkbox" aria-label="Select all channels" checked={allSelected}
                ref={element => { if (element) element.indeterminate = selected.length > 0 && !allSelected }}
                onChange={() => setSelection(allSelected ? [] : rows.map(modeKey))} disabled={disabled}
                className="size-4 align-middle accent-primary" />
              <span className="ms-2 md:sr-only">Select all</span>
            </th>
            <th scope="col" className="py-2 font-normal max-md:hidden">Channel</th>
            <th scope="col" className="w-23/100 py-2 font-normal max-md:hidden">Mode</th>
            <th scope="col" className="w-1/4 py-2 font-normal max-md:hidden">Target</th>
            <th scope="col" className="w-24 py-2 text-right font-normal max-md:hidden"><span className="sr-only">Details</span></th>
          </tr>
        </thead>
        <tbody className="max-md:block">
          {rows.map(row => {
            const key = modeKey(row)
            return <ChannelModeRow key={key} row={row} status={status} targets={targetList}
              targetsBusy={targets.isPending} targetsUnavailable={targets.isError} draft={drafts[key]} error={errors[key]}
              selected={selection.includes(key)} busy={disabled}
              onSelect={() => setSelection(old => old.includes(key) ? old.filter(value => value !== key) : [...old, key])}
              onChange={patch => stage([row], patch)} />
          })}
        </tbody>
      </table>
    </div>
  )
}
