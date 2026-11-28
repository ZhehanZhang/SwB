import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink } from "lucide-react"
import { AlertDialog } from "radix-ui"
import { toast } from "sonner"
import { ClaimLink } from "@/components/claim-link"
import { DataState } from "@/components/data-state"
import { Field, Input } from "@/components/field"
import { FileButton } from "@/components/file-button"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { consoleDocs } from "@/lib/console-docs"
import { settingsQuery } from "@/lib/console-settings"
import { backupNow, claimBackupCredential, downloadMemory, fetchBackup, importMemory, restoreBackup, saveBackup, type BackupConfig, type BackupStatus } from "./api"

const backupKey = ["memory", "backup"] as const
const statusRow = "grid min-h-10 grid-cols-backup-status items-center gap-x-3 border-b py-2"
const credentialError = /authentication|credential|access denied|permission denied|\b401\b|\b403\b/i

function MemoryArchive({ csrf }: { csrf: string }) {
  const client = useQueryClient()
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
    <section aria-label="Memory archives" className="border-t pt-4">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={download.isPending} onClick={() => download.mutate()}>
          {download.isPending ? "Downloading…" : "Download"}
        </Button>
        <FileButton accept=".zip,application/zip" label="Memory archive" onSelect={(file) => upload.mutate(file)} disabled={upload.isPending}>
          {upload.isPending ? "Restoring…" : "Restore from file"}
        </FileButton>
      </div>
      {upload.isError && <p role="alert" className="mt-2 text-body-sm text-destructive">Restore from file failed.</p>}
      {result && <p role="status" className="mt-2 text-body-sm text-muted-foreground">
        {([["created", result.created], ["updated", result.updated], ["unchanged", result.unchanged], ["conflicts", result.conflict], ["skipped", result.skipped]] as const)
          .filter(([, count]) => count > 0).map(([label, count]) => `${count} ${label}`).join(", ") || "Restored"}
      </p>}
    </section>
  )
}

function backupError(detail: string) {
  if (credentialError.test(detail)) return "Check the repository credential."
  if (/non-fast-forward|fetch first|remote.*changes/i.test(detail)) return "Check for changes on the backup branch."
  if (/repository not found|not a git repository|does not appear to be a git repository/i.test(detail)) return "Check the repository address and access."
  return "Check the connection to the repository."
}

function BackupControls({ status, csrf }: { status: BackupStatus; csrf: string }) {
  const client = useQueryClient()
  const [draft, setDraft] = useState<BackupConfig | null>(null)
  const [repositorySaved, setRepositorySaved] = useState(false)
  const configured = Boolean(status.remote && (status.credential_saved || status.enabled || status.last_success_at))
  const ready = configured && status.enabled
  const updateStatus = (value: BackupStatus) => client.setQueryData(backupKey, value)
  const save = useMutation({
    onMutate: () => client.cancelQueries({ queryKey: backupKey }),
    mutationFn: (config: BackupConfig) => saveBackup(config, csrf),
    onSuccess: (value) => {
      setDraft(null)
      updateStatus(value)
    },
  })
  const claim = useMutation({
    gcTime: 0,
    onMutate: () => client.cancelQueries({ queryKey: backupKey }),
    mutationFn: async (config: Parameters<typeof claimBackupCredential>[0]) => {
      const { link, link_expires_at: expiry, ...updated } = await claimBackupCredential(config, csrf)
      return { link, expiry, updated }
    },
    onSuccess: ({ updated }, request) => {
      updateStatus(updated)
      setDraft((current) => current && { ...current, remote: updated.remote })
      if (request.enabled === false) setRepositorySaved(true)
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
  const busy = claim.isPending || save.isPending || run.isPending || restore.isPending
  function edit() {
    setDraft({
      remote: status.remote, branch: status.branch, author_name: status.author_name,
      author_email: status.author_email, enabled: status.enabled || !configured,
    })
    setRepositorySaved(false)
    claim.reset()
    save.reset()
    run.reset()
    restore.reset()
  }

  if (draft) {
    const sameRemote = draft.remote.trim() === status.remote
    const link = sameRemote && claim.data && claim.data.updated.credential_claimed_at === status.credential_claimed_at ? claim.data : null
    const credentialReady = sameRemote && configured && !link
    const ssh = /^git@/.test(draft.remote.trim()) || (sameRemote && /^[^/]+:[^/]/.test(draft.remote))
    function createClaim() {
      if (!draft) return
      claim.mutate({ remote: draft.remote.trim(), ...(sameRemote ? {} : { branch: draft.branch, enabled: false }) })
    }
    return (
      <form className="mt-4 space-y-6" autoComplete="off" onSubmit={(event) => {
        event.preventDefault()
        if (credentialReady && !busy) save.mutate(draft)
      }}>
        <fieldset className="grid min-w-0 gap-3 sm:grid-cols-two-one" disabled={busy}>
          <Field label="Repository address">
            <Input value={draft.remote} autoCapitalize="none" spellCheck={false} onChange={(event) => {
              claim.reset()
              setDraft({ ...draft, remote: event.target.value })
            }} />
          </Field>
          <Field label="Branch">
            <Input value={draft.branch} autoCapitalize="none" spellCheck={false}
              onChange={(event) => setDraft({ ...draft, branch: event.target.value })} />
          </Field>
        </fieldset>
        {(link || credentialReady) && <div role="group" aria-labelledby="backup-credential" className="space-y-1">
          <p id="backup-credential" className="text-label-sm">Credential</p>
          {link ? (
            <>
              <ClaimLink url={link.link} expiresAt={link.expiry} label={ssh ? "Enter key" : "Enter token"} primary />
              <Button variant="ghost" disabled={busy} onClick={createClaim}>New claim link</Button>
            </>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {sameRemote && status.credential_saved && <span role="status" className="text-body-sm text-muted-foreground">Saved</span>}
              <Button variant="outline" disabled={busy} onClick={createClaim}>
                {status.credential_saved ? "Replace credential" : "Add credential"}
              </Button>
            </div>
          )}
        </div>}
        {credentialReady && <fieldset className="grid min-w-0 gap-3 sm:grid-cols-2" disabled={busy}>
          <Field label="Author name">
            <Input value={draft.author_name} maxLength={200}
              onChange={(event) => setDraft({ ...draft, author_name: event.target.value })} />
          </Field>
          <Field label="Author email">
            <Input value={draft.author_email} maxLength={200} inputMode="email" autoCapitalize="none" spellCheck={false}
              onChange={(event) => setDraft({ ...draft, author_email: event.target.value })} />
          </Field>
          <label className="flex w-fit items-center gap-2 text-label-sm sm:col-span-2">
            <input className="accent-primary" type="checkbox" checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
            Automatic backup
          </label>
        </fieldset>}
        <div className="flex flex-wrap gap-2">
          {credentialReady ? <Button type="submit" disabled={busy}>{save.isPending ? "Saving…" : "Save"}</Button>
            : !link && <Button disabled={!draft.remote.trim() || busy} onClick={createClaim}>
              {claim.isPending ? "Creating link…" : sameRemote ? "Create claim link" : "Save repository and create claim link"}
            </Button>}
          <Button type="button" variant="outline" disabled={busy} onClick={() => { setDraft(null); claim.reset() }}>
            {repositorySaved ? "Close" : "Cancel"}
          </Button>
        </div>
        {claim.isError && <p role="alert" className="text-body-sm text-destructive">Check the repository address and branch.</p>}
        {save.isError && <p role="alert" className="text-body-sm text-destructive">Check the backup settings and try again.</p>}
      </form>
    )
  }

  const failed = status.git_available && Boolean(status.last_error)
  const fixCredential = Boolean(status.remote && failed && credentialError.test(status.last_error ?? ""))
  const canRun = ready && status.git_available
  const attemptedAt = status.last_attempt_at ?? (failed ? null : status.last_success_at)
  const savedBackup = status.last_success_at
  const error = !status.git_available ? "Install Git to back up memory."
    : restore.isError ? "Check the repository and try the restore again."
      : run.isError ? "Try starting the backup again."
        : failed && status.last_error ? backupError(status.last_error) : null
  return (
    <div className="mt-3 space-y-4">
      <dl className="text-body-sm" role="status">
        <div className={statusRow}>
          <dt className="text-muted-foreground">Status</dt>
          <dd>{ready ? "Configured" : configured ? "Paused" : "Setup pending"}</dd>
        </div>
        {status.remote && <div className={statusRow}>
          <dt className="text-muted-foreground">Repository</dt>
          <dd className="min-w-0 wrap-anywhere font-mono text-mono-sm">{status.remote}</dd>
        </div>}
        <div className={statusRow}>
          <dt className="text-muted-foreground">Last backup</dt>
          <dd className="flex flex-wrap gap-x-2">
            <span>{failed ? "Failed" : attemptedAt ? "Complete" : "Awaiting first backup"}</span>
            <Time value={attemptedAt} />
          </dd>
        </div>
        {failed && savedBackup && <div className={statusRow}>
          <dt className="text-muted-foreground">Last successful backup</dt>
          <dd><Time value={savedBackup} /></dd>
        </div>}
      </dl>
      {error && <p role="alert" className="text-body-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {canRun && !fixCredential && <Button disabled={busy} onClick={() => { restore.reset(); run.mutate() }}>
          {run.isPending ? "Starting…" : "Back up now"}
        </Button>}
        <Button variant={canRun && !fixCredential ? "outline" : "default"} disabled={busy} onClick={() => {
          edit()
          if (fixCredential) claim.mutate({ remote: status.remote })
        }}>
          {fixCredential ? "Fix credential" : configured ? "Edit settings" : "Configure backup"}
        </Button>
        {configured && <AlertDialog.Root>
          <AlertDialog.Trigger asChild>
            <Button variant="outline" disabled={busy || !status.git_available}>
              {restore.isPending ? "Restoring…" : "Restore from repository"}
            </Button>
          </AlertDialog.Trigger>
          <AlertDialog.Portal>
            <AlertDialog.Overlay className="fixed inset-0 z-40 bg-overlay/50" />
            <AlertDialog.Content aria-describedby={undefined}
              className="fixed top-1/2 left-1/2 z-50 w-inset-dialog max-w-sm -translate-1/2 rounded-lg border bg-card p-5 text-card-foreground shadow-lg">
              <AlertDialog.Title className="text-heading-md">Restore from repository?</AlertDialog.Title>
              <div className="mt-5 flex justify-end gap-2">
                <AlertDialog.Cancel asChild><Button variant="outline">Cancel</Button></AlertDialog.Cancel>
                <AlertDialog.Action asChild>
                  <Button onClick={() => { run.reset(); restore.mutate() }}>Restore</Button>
                </AlertDialog.Action>
              </div>
            </AlertDialog.Content>
          </AlertDialog.Portal>
        </AlertDialog.Root>}
      </div>
    </div>
  )
}

function GitBackup({ csrf }: { csrf: string }) {
  const backup = useQuery({
    queryKey: backupKey,
    queryFn: ({ signal }) => fetchBackup(signal),
    refetchInterval: (query) => query.state.data?.remote ? 3_000 : false,
  })

  return (
    <section aria-label="Git backup">
      {backup.data ? <BackupControls status={backup.data} csrf={csrf} />
        : backup.isError ? <DataState kind="error" message="Backup settings unavailable." onRetry={() => void backup.refetch()} />
          : <DataState kind="loading" />}
    </section>
  )
}

export function MemoryBackup({ csrf }: { csrf: string }) {
  const { data: settings } = useQuery(settingsQuery)
  const { memoryBackup } = consoleDocs({ publicUrl: settings?.public_url })
  return (
    <section aria-labelledby="memory-backup-heading" className="w-full min-w-0 max-w-180 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="memory-backup-heading" className="text-heading-md">Backup</h2>
        <a className="inline-flex items-center gap-1.5 text-body-sm text-muted-foreground hover:text-foreground hover:underline"
          href={memoryBackup} target="_blank" rel="noreferrer">
          Docs<ExternalLink aria-hidden className="size-3" />
        </a>
      </div>
      <GitBackup csrf={csrf} />
      <MemoryArchive csrf={csrf} />
    </section>
  )
}
