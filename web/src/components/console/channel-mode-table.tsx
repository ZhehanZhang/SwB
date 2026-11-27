import { ChannelModeRow } from "@/components/console/channel-mode-row"
import { LogoTile } from "@/components/logo-tile"
import { modeKey, type ModeRow, type ModesStatus } from "@/lib/channel-modes"

export function ChannelModeTable({ label, rows, status }: { label: string; rows: ModeRow[]; status: ModesStatus }) {
  if (rows.length === 0) return null
  return (
    <table aria-label={label} className="mt-2 w-full table-fixed border-collapse text-left text-body-sm">
      <thead className="hidden md:table-header-group">
        <tr className="border-b text-caption text-muted-foreground">
          <th scope="col" className="w-2/5 py-2 font-normal">Channel</th>
          <th scope="col" className="py-2 font-normal">Space</th>
          <th scope="col" className="w-1/3 py-2 font-normal">Mode</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(row => {
          const label = (
            <span className="flex min-w-0 items-center gap-2 wrap-anywhere">
              <LogoTile kind={row.kind === "repo" ? "github" : row.block} className="size-5" />
              <span className="min-w-0 max-md:truncate">{row.name ?? (row.channel || row.space)}</span>
            </span>
          )
          return (
            <ChannelModeRow key={modeKey(row)} row={row} status={status} label={label} mapping={row.project ?? row.space} columns={3}>
              <td className="hidden py-2 pe-2 align-top md:table-cell">{label}</td>
              <td className="hidden py-2 pe-2 align-top wrap-anywhere font-mono text-mono-sm text-muted-foreground md:table-cell">{row.space}</td>
            </ChannelModeRow>
          )
        })}
      </tbody>
    </table>
  )
}
