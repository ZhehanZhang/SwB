import type { ComponentProps } from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 rounded-md text-label-sm whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/85",
        secondary: "bg-muted hover:bg-border",
        outline: "border border-border-strong bg-surface hover:bg-muted",
        ghost: "hover:bg-muted",
        destructive: "bg-destructive text-white hover:bg-destructive/85",
      },
      size: {
        default: "min-h-[34px] px-4",
        sm: "min-h-[26px] px-3",
        icon: "size-[34px]",
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
