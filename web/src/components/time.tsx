import { cn } from "@/lib/utils"

const DAY = 86_400_000
const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" })
const full = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" })

function relative(ms: number) {
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 1) return "now"
  if (minutes < 60) return `${minutes} min ago`
  return `${Math.floor(minutes / 60)} h ago`
}

export function Time({ value, className }: { value: string | number | Date | null | undefined; className?: string }) {
  if (value === null || value === undefined) return null
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return null
  const age = Date.now() - at.getTime()
  return (
    <time dateTime={at.toISOString()} title={full.format(at)} className={cn("text-body-sm tabular-nums text-muted-foreground", className)}>
      {age >= 0 && age < DAY ? relative(age) : date.format(at)}
    </time>
  )
}
