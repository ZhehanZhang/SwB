import { cn } from "@/lib/utils"

const colors = { on: "bg-lamp-on", off: "bg-lamp-off", fault: "bg-destructive" }

export function StatusDot({ state, label, className }: { state: keyof typeof colors; label?: string; className?: string }) {
  return <span role={label ? "img" : undefined} aria-hidden={!label || undefined} aria-label={label} title={label} className={cn("inline-block size-2 shrink-0 rounded-full", colors[state], className)} />
}
