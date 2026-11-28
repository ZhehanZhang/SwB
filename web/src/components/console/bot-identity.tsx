import { useId, useRef, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, ImagePlus, X } from "lucide-react"
import { DataState } from "@/components/data-state"
import { Field, Input } from "@/components/field"
import { SwbIcon } from "@/components/swb-icon"
import { Button } from "@/components/ui/button"
import { ApiError, sessionQuery } from "@/lib/api"
import { botIdentitiesQuery, saveBotIdentity, type BotIdentity, type IdentityChange, type IdentityPlatform } from "@/lib/bot-identities"
import { connectionQuery } from "@/lib/connections"

const imageTypes = ["image/png", "image/jpeg", "image/webp"]
const imageLimit = 2 * 1024 * 1024
const largestSide = 2048
const nameLimits = { discord: 32, telegram: 64, slack: 80 }
const errors: Record<string, string | undefined> = {
  invalid_identity: "Check the bot name.",
  invalid_icon: "Choose a PNG, JPEG or WebP image up to 2 MB with one frame.",
  request_too_large: "Choose an image up to 2 MB.",
  missing_permission: "Check bot permissions.",
  csrf: "Reload the page and try again.",
  identity_unavailable: "Bot identity unavailable.",
}
type Problem = { field?: "name" | "image"; text: string }

function IdentityForm({ platform, identity, csrf, pending, submit, space }: {
  platform: IdentityPlatform
  identity: BotIdentity
  csrf: string | null
  pending: boolean
  submit: (change: IdentityChange) => Promise<unknown>
  space?: { id: string; name: string }
}) {
  const picker = useRef<HTMLInputElement>(null)
  const problemId = useId()
  const [draftName, setDraftName] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [loadFailure, setLoadFailure] = useState<string | null>(null)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [reading, setReading] = useState(false)
  const [action, setAction] = useState<"save" | "reset" | null>(null)
  const name = draftName ?? identity.name
  const source = preview ?? identity.icon_url
  const dirty = name.trim() !== identity.name || preview !== null
  const disabled = !csrf || !identity.can_edit || pending || action !== null || reading
  const imageDisabled = disabled || identity.can_edit_icon === false
  const nameMin = platform === "discord" && !space ? 2 : 1
  const nameMax = nameLimits[platform]

  async function chooseImage(file: File) {
    setProblem(null)
    if (!imageTypes.includes(file.type)) {
      setProblem({ field: "image", text: "Choose a PNG, JPEG or WebP image." })
      return
    }
    if (file.size > imageLimit) {
      setProblem({ field: "image", text: "Choose an image up to 2 MB." })
      return
    }
    setReading(true)
    try {
      const url = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject()
        reader.onerror = reject
        reader.readAsDataURL(file)
      })
      const image = new Image()
      image.src = url
      await image.decode()
      if (Math.max(image.naturalWidth, image.naturalHeight) > largestSide) {
        setProblem({ field: "image", text: "Choose an image up to 2048 pixels on each side." })
        return
      }
      setLoadFailure(null)
      setPreview(url)
    } catch {
      setProblem({ field: "image", text: "Choose a valid image." })
    } finally {
      setReading(false)
    }
  }

  async function save(reset: boolean) {
    if (disabled || (reset ? !identity.overridden : !dirty)) return
    const nextName = name.trim()
    const nameLength = Array.from(nextName).length
    if (!reset && (nameLength < nameMin || nameLength > nameMax)) {
      setProblem({ field: "name", text: `Enter a name with ${nameMin} to ${nameMax} characters.` })
      return
    }
    setProblem(null)
    setAction(reset ? "reset" : "save")
    const placement = space ? { space: space.id } : {}
    try {
      await submit(reset ? { ...placement, reset: true } : {
        ...placement,
        ...(nextName !== identity.name ? { name: nextName } : {}),
        ...(preview ? { icon: preview.slice(preview.indexOf(",") + 1) } : {}),
      })
      setDraftName(null)
      setPreview(null)
    } catch (error) {
      const code = error instanceof ApiError ? error.code : undefined
      setProblem({
        field: code === "invalid_icon" ? "image" : code === "invalid_identity" ? "name" : undefined,
        text: (code && Object.hasOwn(errors, code) ? errors[code] : undefined) ?? (reset ? "Reset failed." : "Save failed."),
      })
    } finally {
      setAction(null)
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void save(false)
  }

  return (
    <form aria-label={space?.name ?? "Bot identity"} onSubmit={onSubmit} className="border-b py-3">
      {space && <h3 className="mb-2 wrap-anywhere text-label-sm">{space.name}</h3>}
      <div className="grid items-end gap-x-4 gap-y-3 md:grid-cols-[auto_minmax(0,1fr)_auto]">
        <div className="flex min-w-0 items-center gap-2">
          <div className="relative shrink-0">
            <span className="grid size-12 place-items-center overflow-hidden rounded-md border border-border bg-surface">
              {loadFailure === source ? (
                <span role="img" aria-label="Current bot icon"><SwbIcon className="size-8" /></span>
              ) : (
                <img src={source} alt={preview ? "Image preview" : "Current bot icon"} className="size-full object-cover"
                  onError={() => setLoadFailure(source)} />
              )}
            </span>
            {preview && (
              <Button size="icon" variant="outline" disabled={disabled} aria-label="Discard image"
                onClick={() => { setPreview(null); setProblem(null) }}
                className="absolute -end-1 -top-1 size-6 [&_svg]:size-3">
                <X aria-hidden />
              </Button>
            )}
          </div>
          <input
            ref={picker} type="file" hidden accept={imageTypes.join(",")} disabled={imageDisabled}
            onChange={event => {
              const file = event.currentTarget.files?.[0]
              event.currentTarget.value = ""
              if (file) void chooseImage(file)
            }}
          />
          <Button variant="outline" disabled={imageDisabled} onClick={() => picker.current?.click()}
            aria-describedby={problem?.field === "image" ? problemId : undefined} className="min-h-9">
            <ImagePlus aria-hidden /> Choose image
          </Button>
        </div>
        <Field label="Name" className="min-w-0 md:pb-1.5">
          <Input value={name} disabled={disabled} autoComplete="off" className="h-9 py-0 disabled:text-muted-foreground"
            aria-invalid={problem?.field === "name" || undefined}
            aria-describedby={problem?.field === "name" ? problemId : undefined}
            onChange={event => {
              setDraftName(event.currentTarget.value)
              if (problem?.field === "name") setProblem(null)
            }} />
        </Field>
        <div className="flex justify-end gap-2 md:pb-1.5">
          <Button variant="ghost" disabled={disabled || !identity.overridden} onClick={() => void save(true)}
            title={space ? "Reset to block identity" : "Reset to SwB default"} className="min-h-9">
            {action === "reset" ? "Resetting…" : "Reset"}
          </Button>
          <Button type="submit" disabled={disabled || !dirty} className="min-h-9">{action === "save" ? "Saving…" : "Save"}</Button>
        </div>
      </div>
      {!identity.can_edit && identity.permissions_url && (
        <div className="mt-2 text-caption text-muted-foreground">
          <a className="inline-flex items-center gap-1 underline underline-offset-2 hover:text-foreground"
            href={identity.permissions_url} target="_blank" rel="noreferrer">
            {platform === "slack" ? "Slack permissions" : "Review permissions"} <ExternalLink aria-hidden className="size-3" />
          </a>
        </div>
      )}
      {problem && <p id={problemId} role="alert" className="mt-2 text-caption text-destructive">{problem.text}</p>}
    </form>
  )
}

export function BotIdentityEditors({ block, platform }: { block: string; platform: IdentityPlatform }) {
  const heading = useId()
  const spaceHeading = useId()
  const client = useQueryClient()
  const { data: session } = useQuery(sessionQuery)
  const query = botIdentitiesQuery(block, platform)
  const result = useQuery(query)
  const csrf = session?.csrf ?? null
  const save = useMutation({
    scope: { id: `bot-identities/${block}` },
    mutationFn: (change: IdentityChange) => {
      if (!csrf) throw new Error("A CSRF token is required")
      return saveBotIdentity(block, platform, csrf, change)
    },
    onMutate: () => client.cancelQueries({ queryKey: query.queryKey }),
    onSuccess: data => {
      client.setQueryData(query.queryKey, data)
      void client.invalidateQueries({ queryKey: connectionQuery(platform).queryKey })
    },
    onError: error => {
      if (error instanceof ApiError && error.code === "missing_permission") {
        void client.invalidateQueries({ queryKey: query.queryKey })
      }
    },
  })

  return (
    <div className="max-w-[720px] space-y-6 md:space-y-8">
      <section aria-labelledby={heading}>
        <h2 id={heading} className="text-heading-md">Bot identity</h2>
        {result.isPending ? <DataState kind="loading" />
          : result.data ? <IdentityForm platform={platform} identity={result.data.identity} csrf={csrf}
            pending={save.isPending} submit={save.mutateAsync} />
            : <DataState kind="error" message="Bot identity unavailable" onRetry={() => void result.refetch()} />}
      </section>
      {Boolean(result.data?.spaces.length) && (
        <section aria-labelledby={spaceHeading}>
          <h2 id={spaceHeading} className="text-heading-md">Space identities</h2>
          {result.data?.spaces.map(space => <IdentityForm key={space.id} space={space} platform={platform}
            identity={space.identity} csrf={csrf} pending={save.isPending} submit={save.mutateAsync} />)}
        </section>
      )}
    </div>
  )
}
