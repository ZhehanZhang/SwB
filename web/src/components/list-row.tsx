import type { ComponentProps, ReactNode } from "react"
import { cn } from "@/lib/utils"

type ListRowProps = Omit<ComponentProps<"div">, "title"> & {
  title?: ReactNode
  meta?: ReactNode
  identifier?: ReactNode
  logo?: ReactNode
  end?: ReactNode
  selected?: boolean
}

export function ListRow({ title, meta, identifier, logo, end, selected, children, className, ...props }: ListRowProps) {
  return (
    <div
      data-selected={selected || undefined}
      className={cn("relative flex min-h-row items-center gap-3 border-b pr-3 pl-4 text-body-sm", selected && "bg-muted before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-accent", className)}
      {...props}
    >
      {logo}
      {children ?? <div className="min-w-0 flex-1">
        {title && <div className="font-semibold wrap-anywhere">{title}</div>}
        {meta && <div className="text-muted-foreground wrap-anywhere">{meta}</div>}
      </div>}
      {identifier && <span className="font-mono text-mono-sm text-muted-foreground">{identifier}</span>}
      {end}
    </div>
  )
}
