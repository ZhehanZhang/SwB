import type { ComponentProps } from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "console-pressable console-press-scale inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-label-sm whitespace-nowrap disabled:pointer-events-none disabled:opacity-50 [&>svg]:size-4 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground press-primary",
        secondary: "bg-muted press-secondary",
        outline: "border border-border-strong bg-surface",
        ghost: "",
        destructive: "bg-destructive text-destructive-foreground press-destructive",
      },
      size: {
        default: "h-control px-4",
        sm: "h-control-sm px-3",
        icon: "size-control",
        "icon-sm": "size-control-sm",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
)

type ButtonProps = ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }

export function Button({ asChild, className, size, type = "button", variant, ...props }: ButtonProps) {
  const Comp = asChild ? Slot.Root : "button"
  return <Comp className={cn(buttonVariants({ size, variant }), className)} {...(asChild ? {} : { type })} {...props} />
}
