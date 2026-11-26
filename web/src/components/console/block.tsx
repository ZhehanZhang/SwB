import type { ReactNode } from "react"
import { Jack, type JackProps } from "./jack"
import { Lamp, type LampState } from "./lamp"
import { LockToggle } from "./lock-toggle"
import { cn } from "@/lib/utils"

type BlockProps = {
  kind: "source" | "agent" | "sink" | "core"
  name: string
  address?: string
  logo?: ReactNode
  status: LampState
  statusLabel?: string
  shared: boolean
  onSharedChange?: (shared: boolean) => void
  onInspect?: () => void
  input?: JackProps
  output?: JackProps
  selected?: boolean
  className?: string
}

const statusLabels = { on: "Online", waiting: "Needs attention", off: "Offline" }

export function Block({
  kind,
  name,
  address,
  logo,
  status,
  statusLabel = statusLabels[status],
  shared,
  onSharedChange,
  onInspect,
  input,
  output,
  selected,
  className,
}: BlockProps) {
  const core = kind === "core"
  return (
    <div
      data-kind={kind}
      className={cn(
        "relative flex min-h-20 min-w-48 items-center gap-2 rounded-md border bg-card px-5 py-2 text-card-foreground",
        core && "border-console-line bg-console text-console-foreground",
        selected && "border-lamp",
        className,
      )}
    >
      {input && kind !== "source" && (
        <Jack {...input} className={cn("absolute left-0 -translate-x-1/2", input.className)} />
      )}
      {logo && <span aria-hidden className="grid size-6 shrink-0 place-items-center [&_svg]:size-6">{logo}</span>}
      <div className="min-w-0 flex-1">
        {onInspect ? (
          <button type="button" onClick={onInspect} className="block max-w-full truncate rounded-sm font-semibold hover:underline">
            {name}
          </button>
        ) : <div className="truncate font-semibold">{name}</div>}
        {address && (
          <div className={cn("truncate font-mono text-xs", core ? "text-console-muted" : "text-muted-foreground")} title={address}>
            {address}
          </div>
        )}
      </div>
      <Lamp state={status} label={statusLabel} />
      <LockToggle
        shared={shared}
        resource={name}
        onSharedChange={onSharedChange}
        className={core ? cn("text-console-foreground hover:bg-console-line", shared && "[&_svg]:text-lamp") : undefined}
      />
      {output && (kind === "source" || core) && (
        <Jack {...output} className={cn("absolute right-0 translate-x-1/2", output.className)} />
      )}
    </div>
  )
}
