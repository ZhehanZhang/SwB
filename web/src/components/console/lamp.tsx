import { cn } from "@/lib/utils"

export type LampState = "on" | "off" | "fault"

const colors: Record<LampState, string> = { on: "bg-lamp-on", off: "bg-lamp-off", fault: "bg-destructive" }

export function Lamp({ state, label }: { state: LampState; label?: string }) {
  return (
    <span className="inline-grid size-2.5 shrink-0 place-items-center">
      <span aria-hidden className={cn("size-2 rounded-full", colors[state])} />
      {label && <span className="sr-only">{label}</span>}
    </span>
  )
}
