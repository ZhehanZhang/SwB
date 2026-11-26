import { cn } from "@/lib/utils"

export type JackState = "empty" | "intake" | "route" | "sink"

const colors: Record<Exclude<JackState, "empty">, string> = {
  intake: "bg-cord-intake",
  route: "bg-cord-route",
  sink: "bg-cord-sink",
}

export function Jack({ state = "empty", className }: { state?: JackState; className?: string }) {
  return (
    <span aria-hidden className={cn("inline-grid size-4 shrink-0 place-items-center", className)}>
      <span className="grid size-3.5 place-items-center rounded-full bg-socket">
        {state !== "empty" && <span className={cn("size-1 rounded-full", colors[state])} />}
      </span>
    </span>
  )
}
