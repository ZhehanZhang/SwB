import { useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertDialog } from "radix-ui"
import { toast } from "sonner"
import { DataState } from "@/components/data-state"
import { Field, Input } from "@/components/field"
import { Button } from "@/components/ui/button"
import { backupNow, fetchBackup, restoreBackup, saveBackup, type BackupConfig, type BackupStatus } from "./api"

const backupKey = ["memory", "backup"] as const
const dateFormat = new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" })

function BackupForm({ status, csrf }: { status: BackupStatus; csrf: string }) {
  const client = useQueryClient()
  const [unsaved, setDraft] = useState<BackupConfig | null>(null)
  const draft = unsaved ?? {
    remote: status.remote,
    branch: status.branch,
    author_name: status.author_name,
    author_email: status.author_email,
    enabled: status.enabled,
  }
  const [credentialEntered, setCredentialEntered] = useState(false)
  const credentialInput = useRef<HTMLInputElement>(null)
  const dirty = credentialEntered || draft.remote !== status.remote || draft.branch !== status.branch
    || draft.author_name !== status.author_name || draft.author_email !== status.author_email || draft.enabled !== status.enabled
  const ssh = /^git@/.test(draft.remote.trim()) || (/^[^/]+:[^/]/.test(draft.remote) && draft.remote === status.remote)
  const file = draft.remote.trim().startsWith("file://")
  const credentialSaved = draft.remote === status.remote && status.credential_saved

  const updateStatus = (value: BackupStatus) => client.setQueryData(backupKey, value)
  const save = useMutation({
    onMutate: () => client.cancelQueries({ queryKey: backupKey }),
    mutationFn: async () => {
      const input = credentialInput.current
      const credential = ssh ? await input?.files?.[0]?.text() : input?.value
      return saveBackup(draft, csrf, credential || undefined)
    },
    onSuccess: (value) => {
      setDraft(null)
      updateStatus(value)
      if (credentialInput.current) credentialInput.current.value = ""
      setCredentialEntered(false)
      toast.success("Backup settings saved.")
    },
  })
  const run = useMutation({
    mutationFn: () => backupNow(csrf),
    onSuccess: (value) => {
      updateStatus(value)
      void client.invalidateQueries({ queryKey: backupKey })
    },
  })
  const restore = useMutation({
    mutationFn: () => restoreBackup(csrf),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["memory"] })
      toast.success("Repository restore complete.")
    },
  })
  const busy = save.isPending || run.isPending || restore.isPending
  const saveError = save.isError ? "Backup settings update failed." : null
  const runError = run.isError ? "Backup request failed." : null
  const restoreError = restore.isError ? "Restore from repository failed." : null
  const error = saveError || runError || restoreError || status.last_error

  function clearErrors() {
    save.reset()
    run.reset()
    restore.reset()
  }

  return (
    <form className="mt-3 space-y-4" autoComplete="off" onSubmit={(event) => {
      event.preventDefault()
      if (!dirty || busy) return
      clearErrors()
      save.mutate()
    }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1 sm:col-span-2">
          <Field label="Repository">
            <Input value={draft.remote} disabled={busy} aria-describedby="memory-backup-repository"
              autoCapitalize="none" spellCheck={false}
              onChange={(event) => {
                clearErrors()
                setDraft({ ...draft, remote: event.target.value })
                if (credentialInput.current) credentialInput.current.value = ""
                setCredentialEntered(false)
              }} />
          </Field>
          <p id="memory-backup-repository" className="text-body-sm text-muted-foreground">Everything in memory goes to this repository.</p>
        </div>
        <Field label="Branch">
          <Input value={draft.branch} disabled={busy} autoCapitalize="none" spellCheck={false}
            onChange={(event) => setDraft({ ...draft, branch: event.target.value })} />
        </Field>
        <Field label={<>Credential{ssh && credentialSaved && <span className="ml-2 text-muted-foreground">Saved</span>}</>}>
          {ssh ? (
            <Input key="ssh" ref={credentialInput} type="file" accept=".pem,.key,.txt" disabled={busy}
              onChange={(event) => setCredentialEntered(!!event.currentTarget.files?.length)} />
          ) : (
            <Input key="token" ref={credentialInput} name="credential" type="password" autoComplete="new-password"
              autoCapitalize="none" spellCheck={false} disabled={busy || file}
              placeholder={credentialSaved ? "Saved" : undefined}
              onChange={(event) => setCredentialEntered(!!event.currentTarget.value)} />
          )}
        </Field>
        <Field label="Author name">
          <Input value={draft.author_name} disabled={busy} maxLength={200}
            onChange={(event) => setDraft({ ...draft, author_name: event.target.value })} />
        </Field>
        <Field label="Author email">
          <Input value={draft.author_email} disabled={busy} maxLength={200} inputMode="email"
            autoCapitalize="none" spellCheck={false} onChange={(event) => setDraft({ ...draft, author_email: event.target.value })} />
        </Field>
      </div>
      <label className="flex w-fit items-center gap-2 text-label-sm">
        <input className="accent-primary" type="checkbox" checked={draft.enabled} disabled={busy || (!draft.remote.trim() && !draft.enabled)}
          onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
        Automatic backup
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!dirty || busy}>{save.isPending ? "Saving…" : "Save"}</Button>
        <Button variant="outline" disabled={dirty || busy || !status.enabled || !status.git_available}
          onClick={() => { clearErrors(); run.mutate() }}>
          {run.isPending ? "Starting…" : "Back up now"}
        </Button>
        <AlertDialog.Root>
          <AlertDialog.Trigger asChild>
            <Button variant="outline" disabled={dirty || busy || !status.remote || !status.git_available}>
              {restore.isPending ? "Restoring…" : "Restore from repository"}
            </Button>
          </AlertDialog.Trigger>
          <AlertDialog.Portal>
            <AlertDialog.Overlay className="fixed inset-0 z-40 bg-overlay/50" />
            <AlertDialog.Content aria-describedby={undefined}
              className="fixed top-1/2 left-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-1/2 rounded-lg border bg-card p-5 text-card-foreground shadow-lg">
              <AlertDialog.Title className="text-heading-md">Restore from repository?</AlertDialog.Title>
              <div className="mt-5 flex justify-end gap-2">
                <AlertDialog.Cancel asChild><Button variant="outline">Cancel</Button></AlertDialog.Cancel>
                <AlertDialog.Action asChild>
                  <Button onClick={() => { clearErrors(); restore.mutate() }}>Restore</Button>
                </AlertDialog.Action>
              </div>
            </AlertDialog.Content>
          </AlertDialog.Portal>
        </AlertDialog.Root>
      </div>
      {error ? <p role="alert" className="text-body-sm wrap-anywhere text-destructive">{error}</p>
        : status.last_success_at && status.last_commit ? (
          <p role="status" className="flex flex-wrap gap-x-2 text-body-sm text-muted-foreground">
            Last backup <time dateTime={status.last_success_at}>{dateFormat.format(new Date(status.last_success_at))}</time>
            <code title={status.last_commit}>{status.last_commit.slice(0, 7)}</code>
          </p>
        ) : null}
    </form>
  )
}

export function GitBackup({ csrf }: { csrf: string }) {
  const backup = useQuery({
    queryKey: backupKey,
    queryFn: ({ signal }) => fetchBackup(signal),
    refetchInterval: (query) => query.state.data?.enabled ? 5_000 : false,
  })

  return (
    <section aria-labelledby="memory-backup-heading" className="min-w-0 border-t pt-4 md:col-start-2">
      <h2 id="memory-backup-heading" className="text-heading-md">Git backup</h2>
      <div className="max-w-[720px]">
        {backup.data ? <BackupForm status={backup.data} csrf={csrf} />
          : backup.isError ? <DataState kind="error" message="Backup settings unavailable." onRetry={() => void backup.refetch()} />
            : <DataState kind="loading" />}
      </div>
    </section>
  )
}
