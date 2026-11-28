import { Logo } from "@/components/logo"

const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" })
const fullDate = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" })

export function scopeName(scope: string) {
  return scope.replace(/^(?:project|agent):/, "")
}

export function NoteTime({ date, short = false }: { date: string | null; short?: boolean }) {
  if (!date) return null
  return <time dateTime={date} title={fullDate.format(new Date(date))}>{(short ? shortDate : fullDate).format(new Date(date))}</time>
}

export function Author({ source, compact = false }: { source: string; compact?: boolean }) {
  if (!source) return null
  const owner = source.startsWith("admin:") || source === "key:owner"
  const name = owner ? "Owner" : source
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Logo kind={owner ? "owner" : source} className={compact ? "size-4" : "size-5"} />
      <span className="truncate" title={name}>{name}</span>
    </span>
  )
}
