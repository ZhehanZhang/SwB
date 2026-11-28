import { useRef, type ReactNode } from "react"
import { Button } from "@/components/ui/button"

type FileButtonProps = {
  accept: string
  label: string
  onSelect: (file: File) => void
  disabled?: boolean
  children: ReactNode
}

export function FileButton({ accept, label, onSelect, disabled, children }: FileButtonProps) {
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <Button variant="outline" disabled={disabled} onClick={() => input.current?.click()}>{children}</Button>
      <input
        ref={input}
        type="file"
        hidden
        accept={accept}
        aria-label={label}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0]
          event.currentTarget.value = ""
          if (file) onSelect(file)
        }}
      />
    </>
  )
}
