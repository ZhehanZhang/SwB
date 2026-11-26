import type { ComponentProps, ReactNode } from "react"
import { cn } from "@/lib/utils"

export function Field({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return <label className={cn("grid gap-1 text-label-sm", className)}><span>{label}</span>{children}</label>
}

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn("min-h-9 w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-body-md placeholder:text-subtle-foreground", className)} {...props} />
}
