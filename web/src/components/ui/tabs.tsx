import type { ComponentProps } from "react"
import { Tabs as Primitive } from "radix-ui"
import { cn } from "@/lib/utils"

export const Tabs = Primitive.Root
export const TabsContent = Primitive.Content

export function TabsList({ className, ...props }: ComponentProps<typeof Primitive.List>) {
  return <Primitive.List className={cn("inline-flex flex-wrap items-center gap-1 rounded-md bg-muted p-1", className)} {...props} />
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof Primitive.Trigger>) {
  return <Primitive.Trigger className={cn("rounded-sm px-3 py-1 text-label-sm text-muted-foreground data-[state=active]:bg-surface data-[state=active]:text-foreground data-[state=active]:shadow-xs", className)} {...props} />
}
