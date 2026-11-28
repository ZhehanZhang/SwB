import { StatusDot } from "@/components/status-dot"

export type LampState = "on" | "off" | "fault"

export function Lamp({ state, label }: { state: LampState; label?: string }) {
  return (
    <span className="inline-grid size-2.5 shrink-0 place-items-center">
      <StatusDot state={state} label={label} />
    </span>
  )
}
