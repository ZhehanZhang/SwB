import { Logo } from "@/components/logo"

const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" })
const fullDate = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" })

export function scopeName(scope: string) {
  if (scope.startsWith("agent:")) return `Agent ${scope.slice(6)}`
  return scope.replace(/^project:/, "")
}

export function NoteTime({ date, short = false }: { date: string | null; short?: boolean }) {
  if (!date) return null
  return <time dateTime={date} title={fullDate.format(new Date(date))}>{(short ? shortDate : fullDate).format(new Date(date))}</time>
}

export function Author({ source }: { source: string }) {
  if (!source) return null
  const name = source.startsWith("admin:") ? "Owner" : source
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Logo kind={source} className="size-5" />
      <span className="truncate" title={name}>{name}</span>
    </span>
  )
}
