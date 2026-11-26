import { cn } from "@/lib/utils"

export type LampState = "on" | "waiting" | "off"

const colors: Record<LampState, string> = { on: "bg-ok", waiting: "bg-lamp", off: "bg-lamp-off" }

export function Lamp({ state, label, className }: { state: LampState; label?: string; className?: string }) {
  return (
    <span className="inline-flex shrink-0 items-center">
      <span aria-hidden className={cn("size-2 rounded-full", colors[state], className)} />
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}
