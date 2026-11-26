import { cn } from "@/lib/utils"

export type LampState = "on" | "off" | "fault"

const colors: Record<LampState, string> = { on: "bg-lamp-on", off: "bg-lamp-off", fault: "bg-destructive" }

export function Lamp({ state, label, className }: { state: LampState; label?: string; className?: string }) {
  return (
    <span className="inline-flex shrink-0 items-center">
      <span aria-hidden className={cn("size-2.5 rounded-full", colors[state], className)} />
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}
