import { cn } from "@/lib/utils"

export type JackState = "empty" | "intake" | "route" | "sink"

const colors: Record<JackState, string> = {
  empty: "bg-socket",
  intake: "bg-cord-intake",
  route: "bg-cord-route",
  sink: "bg-cord-sink",
}

export function Jack({ state = "empty", className }: { state?: JackState; className?: string }) {
  return (
    <span aria-hidden className={cn("grid size-4 shrink-0 place-items-center rounded-full", colors[state], className)}>
      <span className="size-1 rounded-full bg-socket-inner/40" />
    </span>
  )
}
