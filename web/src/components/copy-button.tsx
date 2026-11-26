import { useEffect, useState } from "react"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const timeout = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timeout)
  }, [copied])
  const Icon = copied ? Check : Copy
  return (
    <Button
      variant="ghost" size="icon" className="size-6 text-muted-foreground hover:text-foreground"
      aria-label={copied ? "Copied" : label} title={copied ? "Copied" : label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setCopied(true)
          toast.success("Copied")
        } catch {
          toast.error("Couldn’t copy.")
        }
      }}
    >
      <Icon aria-hidden />
    </Button>
  )
}
