import type { ReactElement } from "react"
import { AlertDialog } from "radix-ui"
import { Button } from "@/components/ui/button"

export function ConfirmAction({ title, action, subject, onConfirm, children }: {
  title: string; action: string; subject?: string; onConfirm: () => void; children: ReactElement
}) {
  return (
    <AlertDialog.Root>
      <AlertDialog.Trigger asChild>{children}</AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-40 bg-overlay/50" />
        <AlertDialog.Content className="fixed top-1/2 left-1/2 z-50 w-inset-dialog max-w-sm -translate-1/2 rounded-lg border bg-card p-5 text-card-foreground shadow-lg">
          <AlertDialog.Title className="text-heading-md wrap-anywhere">{title}</AlertDialog.Title>
          <AlertDialog.Description className={subject ? "mt-1 wrap-anywhere text-body-sm text-muted-foreground" : "sr-only"}>
            {subject ?? title}
          </AlertDialog.Description>
          <div className="mt-5 flex justify-end gap-2">
            <AlertDialog.Cancel asChild><Button variant="outline">Cancel</Button></AlertDialog.Cancel>
            <AlertDialog.Action asChild><Button variant="destructive" onClick={onConfirm}>{action}</Button></AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  )
}
