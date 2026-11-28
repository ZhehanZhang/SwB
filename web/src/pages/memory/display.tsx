import { Logo } from "@/components/logo"

export function scopeName(scope: string) {
  return scope.replace(/^(?:project|agent):/, "")
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
