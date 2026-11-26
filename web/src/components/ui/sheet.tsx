import { useRef, type ComponentProps, type ReactElement } from "react"
import { X } from "lucide-react"
import { Dialog } from "radix-ui"
import { Button } from "@/components/ui/button"

type SheetProps = Pick<ComponentProps<typeof Dialog.Root>, "open" | "onOpenChange" | "children"> & {
  title: string
  description?: string
  trigger?: ReactElement
}

export function Sheet({ open, onOpenChange, title, description, trigger, children }: SheetProps) {
  const returnFocus = useRef<HTMLElement | SVGElement | null>(null)
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger && <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>}
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <Dialog.Content
          {...(!description && { "aria-describedby": undefined })}
          className="fixed inset-x-0 bottom-0 z-50 flex max-h-[90dvh] flex-col rounded-t-lg border-t bg-card text-card-foreground md:inset-y-0 md:right-0 md:left-auto md:max-h-none md:w-[340px] md:rounded-none md:border-t-0 md:border-l"
          onOpenAutoFocus={() => {
            const focused = document.activeElement
            returnFocus.current = focused instanceof HTMLElement || focused instanceof SVGElement ? focused : null
          }}
          onCloseAutoFocus={(event) => {
            if (!trigger && returnFocus.current?.isConnected) {
              event.preventDefault()
              returnFocus.current.focus()
            }
          }}
        >
          <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
            <div className="min-w-0 self-center">
              <Dialog.Title className="font-display text-lg leading-tight font-bold break-words">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-1 text-sm text-muted-foreground">{description}</Dialog.Description>}
            </div>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close inspector">
                <X aria-hidden />
              </Button>
            </Dialog.Close>
          </div>
          <div className="min-h-0 overflow-y-auto px-4 pt-4 pb-[max(1rem,env(safe-area-inset-bottom))]">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
