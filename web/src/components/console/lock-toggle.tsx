import { Lock, LockOpen } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type LockToggleProps = {
  shared: boolean
  resource: string
  onSharedChange?: (shared: boolean) => void
  className?: string
}

export function LockToggle({ shared, resource, onSharedChange, className }: LockToggleProps) {
  const Icon = shared ? LockOpen : Lock
  const status = shared ? "Shared" : "Private"

  if (!onSharedChange) {
    return (
      <span className={cn("inline-grid size-10 shrink-0 place-items-center", className)} title={status}>
        <Icon aria-hidden className="size-4" />
        <span className="sr-only">{status} {resource}</span>
      </span>
    )
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className={className}
      aria-label={`Share ${resource}`}
      aria-pressed={shared}
      title={status}
      onClick={() => onSharedChange(!shared)}
    >
      <Icon aria-hidden className={cn(shared && "text-ring")} />
    </Button>
  )
}
