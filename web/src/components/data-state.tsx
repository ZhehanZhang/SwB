import { LoaderCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export type DataStateProps = (
  | { kind: "loading"; message?: string }
  | { kind: "error"; message?: string; onRetry?: () => void }
  | { kind: "empty"; message?: string }
) & { className?: string }

const messages = { loading: "Loading…", error: "Data unavailable.", empty: "0 results" }

export function DataState(props: DataStateProps) {
  const { kind, className, message = messages[kind] } = props
  return (
    <div className={cn("flex flex-wrap items-center gap-2 py-5 text-sm text-muted-foreground", className)}>
      <span className="flex items-center gap-2" role={kind === "error" ? "alert" : "status"}>
        {kind === "loading" && <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" />}
        {message}
      </span>
      {props.kind === "error" && props.onRetry && (
        <Button size="sm" variant="outline" onClick={props.onRetry}>Retry</Button>
      )}
    </div>
  )
}
