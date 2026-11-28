import type { ComponentProps, ReactNode } from "react"
import { ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"

type DisclosureProps = Omit<ComponentProps<"details">, "title"> & {
  summary: ReactNode
  summaryProps?: ComponentProps<"summary">
}

export function Disclosure({ summary, summaryProps, className, children, ...props }: DisclosureProps) {
  return (
    <details className={cn("group", className)} {...props}>
      <summary
        {...summaryProps}
        className={cn("flex w-fit cursor-pointer list-none items-center gap-1 rounded-sm text-body-sm font-medium [&::-webkit-details-marker]:hidden", summaryProps?.className)}
      >
        <ChevronRight aria-hidden className="size-4 text-muted-foreground group-open:rotate-90 motion-safe:transition-transform" />
        {summary}
      </summary>
      {children}
    </details>
  )
}
