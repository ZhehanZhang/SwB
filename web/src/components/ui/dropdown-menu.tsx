import type { ComponentProps } from "react"
import { Check } from "lucide-react"
import { DropdownMenu as Menu } from "radix-ui"
import { cn } from "@/lib/utils"

export const DropdownMenu = Menu.Root
export const DropdownMenuTrigger = Menu.Trigger
export const DropdownMenuRadioGroup = Menu.RadioGroup
export const DropdownMenuLabel = Menu.Label
export const DropdownMenuSeparator = Menu.Separator

export function DropdownMenuContent({ className, sideOffset = 8, ...props }: ComponentProps<typeof Menu.Content>) {
  return (
    <Menu.Portal>
      <Menu.Content
        className={cn(
          "z-50 max-h-[var(--radix-dropdown-menu-content-available-height)] min-w-40 overflow-y-auto rounded-md border bg-popover p-1 text-sm text-popover-foreground",
          className,
        )}
        sideOffset={sideOffset}
        {...props}
      />
    </Menu.Portal>
  )
}

const itemClass = "flex min-h-10 cursor-default items-center gap-2 rounded-sm px-2.5 outline-none select-none data-highlighted:bg-muted data-disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0"

export function DropdownMenuItem({ className, ...props }: ComponentProps<typeof Menu.Item>) {
  return <Menu.Item className={cn(itemClass, className)} {...props} />
}

export function DropdownMenuRadioItem({ children, className, ...props }: ComponentProps<typeof Menu.RadioItem>) {
  return (
    <Menu.RadioItem className={cn(itemClass, className)} {...props}>
      {children}
      <Menu.ItemIndicator className="ml-auto">
        <Check aria-hidden />
      </Menu.ItemIndicator>
    </Menu.RadioItem>
  )
}
