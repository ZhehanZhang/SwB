import type { ComponentProps } from "react"
import type { CordKind } from "./cord"
import { cn } from "@/lib/utils"

export type JackState = "idle" | "lit" | "patched" | "selected"
export type JackProps = Omit<ComponentProps<"button">, "children" | "type" | "aria-label"> & {
  kind: CordKind
  state?: JackState
  label: string
}

const socketColors: Record<JackState, string> = {
  idle: "bg-jack",
  lit: "bg-lamp-soft",
  patched: "bg-jack",
  selected: "bg-lamp",
}
const cordColors: Record<CordKind, string> = {
  source: "bg-cord-source",
  agent: "bg-cord-agent",
  sink: "bg-cord-sink",
}

export function Jack({ className, kind, state = "idle", label, ...props }: JackProps) {
  return (
    <button
      {...props}
      type="button"
      aria-label={label}
      aria-pressed={state === "selected"}
      data-state={state}
      className={cn("grid size-11 place-items-center rounded-sm md:size-8", className)}
    >
      <span aria-hidden className={cn("grid size-[18px] place-items-center rounded-full", socketColors[state])}>
        {state !== "idle" && (
          <span className={cn("size-1.5 rounded-full", cordColors[kind])} />
        )}
      </span>
    </button>
  )
}
