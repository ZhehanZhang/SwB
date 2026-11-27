import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden="true" className={cn("block shrink-0 rounded-sm bg-border", className)} />
}

export function LoadingSkeleton({ message, className, children }: {
  message: string
  className?: string
  children: ReactNode
}) {
  return (
    <div role="status" className={className}>
      <span className="sr-only">{message}</span>
      <div aria-hidden="true">{children}</div>
    </div>
  )
}
