import { useRef } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Switch } from "radix-ui"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { downloadMemory, fetchMemorySettings, importMemory, saveMemorySync } from "./api"

const settingsKey = ["memory", "settings"] as const

export function MemoryHeader({ csrf }: { csrf: string }) {
  const client = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: ({ signal }) => fetchMemorySettings(signal),
  })
  const sync = useMutation({
    mutationFn: (enabled: boolean) => saveMemorySync(enabled, csrf),
    onSuccess: (data) => client.setQueryData(settingsKey, data),
    onError: () => toast.error("Memory sync update failed."),
  })
  const download = useMutation({
    mutationFn: downloadMemory,
    onError: () => toast.error("Download failed."),
  })
  const upload = useMutation({
    mutationFn: (file: File) => importMemory(file, csrf),
    onSuccess: () => client.invalidateQueries({ queryKey: ["memory"] }),
  })
  const result = upload.data

  return (
    <section aria-label="Memory controls" className="min-w-0 border-b pb-3 md:col-span-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={download.isPending} onClick={() => download.mutate()}>
            {download.isPending ? "Downloading…" : "Download"}
          </Button>
          <Button variant="outline" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
            {upload.isPending ? "Restoring…" : "Restore from file"}
          </Button>
          <input ref={fileInput} type="file" hidden accept=".zip,application/zip" aria-label="Memory archive"
            onChange={(event) => {
              const file = event.currentTarget.files?.[0]
              event.currentTarget.value = ""
              if (file) upload.mutate(file)
            }} />
        </div>
        <label className="flex min-h-[34px] items-center gap-2 text-label-sm md:ml-auto">
          Memory sync
          <Switch.Root checked={settings.data?.sync ?? false} disabled={!settings.data || sync.isPending}
            onCheckedChange={(checked) => sync.mutate(checked)}
            className="console-pressable flex h-5 w-9 shrink-0 items-center rounded-full border border-border-strong bg-muted p-0.5 data-[state=checked]:border-primary data-[state=checked]:bg-primary disabled:opacity-50">
            <Switch.Thumb className="block size-3.5 rounded-full border border-border-strong bg-surface transition-transform data-[state=checked]:translate-x-4 data-[state=checked]:border-transparent motion-reduce:transition-none" />
          </Switch.Root>
        </label>
      </div>
      {settings.isError && <p role="alert" className="mt-2 text-body-sm text-destructive">Memory sync unavailable.</p>}
      {upload.isError && <p role="alert" className="mt-2 text-body-sm text-destructive">Restore from file failed.</p>}
      {result && <p role="status" className="mt-2 text-body-sm text-muted-foreground">
        {result.created} created, {result.updated} updated, {result.unchanged} unchanged, {result.conflict} conflicts, {result.skipped} skipped
      </p>}
    </section>
  )
}
