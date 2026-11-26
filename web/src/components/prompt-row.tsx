import { ChevronRight, Copy } from "lucide-react"
import { Collapsible } from "radix-ui"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"

type PromptRowProps = {
  title: string
  version?: string
  prompt: string
}

export function PromptRow({ title, version, prompt }: PromptRowProps) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(prompt)
      toast.success("Copied")
    } catch {
      toast.error("Couldn’t copy the prompt")
    }
  }

  return (
    <Collapsible.Root className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 border-b py-2">
      <Collapsible.Trigger className="group flex min-h-11 min-w-0 items-center gap-2 rounded-sm text-left text-sm">
        <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none group-data-[state=open]:rotate-90" />
        <span className="min-w-0 break-words font-medium">{title}</span>
        {version && <span className="text-xs text-muted-foreground">{version}</span>}
      </Collapsible.Trigger>
      <Button variant="outline" size="sm" onClick={copy} aria-label={`Copy ${title}`}>
        <Copy aria-hidden /> Copy
      </Button>
      <Collapsible.Content className="col-span-2 min-w-0 py-3">
        <pre className="max-h-72 overflow-auto font-mono text-xs leading-relaxed break-words whitespace-pre-wrap" tabIndex={0}>
          {prompt}
        </pre>
      </Collapsible.Content>
    </Collapsible.Root>
  )
}
