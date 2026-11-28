import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Switch } from "radix-ui"
import { toast } from "sonner"
import { fetchMemorySettings, saveMemorySync } from "./api"

const settingsKey = ["memory", "settings"] as const

export function MemorySync({ csrf }: { csrf: string }) {
  const client = useQueryClient()
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: ({ signal }) => fetchMemorySettings(signal),
  })
  const sync = useMutation({
    mutationFn: (enabled: boolean) => saveMemorySync(enabled, csrf),
    onSuccess: (data) => client.setQueryData(settingsKey, data),
    onError: () => toast.error("Memory sync update failed."),
  })
  return (
    <>
      <label className="flex min-h-[34px] items-center gap-2 text-label-sm"
        title={settings.isError ? "Memory sync unavailable." : undefined}>
        Memory sync
        <Switch.Root checked={settings.data?.sync ?? false} disabled={!settings.data || sync.isPending}
          onCheckedChange={(checked) => sync.mutate(checked)}
          className="console-pressable flex h-5 w-9 shrink-0 items-center rounded-full border border-border-strong bg-muted p-0.5 data-[state=checked]:border-primary data-[state=checked]:bg-primary disabled:opacity-50">
          <Switch.Thumb className="block size-3.5 rounded-full border border-border-strong bg-surface transition-transform data-[state=checked]:translate-x-4 data-[state=checked]:border-transparent motion-reduce:transition-none" />
        </Switch.Root>
      </label>
      {settings.isError && <span role="alert" className="sr-only">Memory sync unavailable.</span>}
    </>
  )
}
