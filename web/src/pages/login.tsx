import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound } from "lucide-react"
import { Navigate, useLocation } from "react-router-dom"
import { PRODUCT_NAME } from "@/app/brand"
import { DataState } from "@/components/data-state"
import { Field, Input } from "@/components/field"
import { SwbIcon } from "@/components/swb-icon"
import { Button } from "@/components/ui/button"
import { ApiError, sessionQuery } from "@/lib/api"
import { addPasskey, exchangeLinkFromLocation, oidcStart, safeNext, signInWithPasskey, signInWithSetupCode, supportsPasskeys } from "@/lib/owner-auth"

export function LoginPage() {
  const { search } = useLocation()
  const params = new URLSearchParams(search)
  const next = safeNext(params.get("next"))
  const client = useQueryClient()
  const session = useQuery(sessionQuery)
  const refreshSession = useCallback(async () => {
    await client.cancelQueries({ queryKey: sessionQuery.queryKey })
    const fresh = await client.fetchQuery({ ...sessionQuery, staleTime: 0 })
    if (!fresh.signed_in) throw new Error("Sign-in did not start a session")
  }, [client])
  const exchange = useRef<Promise<void>>(undefined)
  const [linkPending, setLinkPending] = useState(() => new URLSearchParams(location.hash.slice(1)).has("t"))
  const [linkError, setLinkError] = useState("")
  const [name, setName] = useState("")
  const [enrollmentDone, setEnrollmentDone] = useState(false)
  const [setupPending, setSetupPending] = useState(false)
  const [setupError, setSetupError] = useState("")
  const passkeysSupported = supportsPasskeys()

  useLayoutEffect(() => {
    const pending = exchange.current ??= exchangeLinkFromLocation()
    if (!pending) return
    let active = true
    void pending.then(async () => {
      if (!active) return
      await refreshSession()
    }).catch((error: unknown) => {
      if (!active) return
      const code = error instanceof ApiError ? error.code ?? "" : ""
      const messages: Record<string, string> = {
        link_invalid: "Check your sign-in link.",
        link_used: "This link was used. Ask your agent for a new link.",
        link_expired: "This link expired. Ask your agent for a new link.",
      }
      setLinkError(messages[code] ?? "Try signing in again.")
    }).finally(() => {
      if (active) setLinkPending(false)
    })
    return () => { active = false }
  }, [refreshSession])

  useEffect(() => {
    document.title = `Sign in | ${PRODUCT_NAME}`
  }, [])

  const signIn = useMutation({ mutationFn: async () => {
    await signInWithPasskey()
    await refreshSession()
  } })
  const register = useMutation({
    mutationFn: () => addPasskey(session.data?.csrf ?? null, name.trim() || "Passkey"),
    onSuccess: () => {
      setEnrollmentDone(true)
      void client.invalidateQueries({ queryKey: sessionQuery.queryKey })
    },
  })

  const accessOwner = session.data?.signed_in && session.data.via === "access"
  const enroll = session.data?.signed_in && session.data.methods.passkeys === 0
  if (accessOwner || (!linkPending && session.data?.signed_in && (!enroll || enrollmentDone))) {
    return <Navigate to={next} replace />
  }

  const hasPasskey = !!session.data?.methods.passkeys
  const oidc = session.data?.methods.oidc
  const canSetUp = session.data?.methods.setup === true
  const error = linkError || (signIn.isError ? "Passkey sign-in failed." : "")
    || (params.get("error") === "oidc_failed" ? "Sign-in with your provider failed." : "")
  const loading = linkPending || session.isPending

  function enrollPasskey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    register.mutate()
  }

  async function enterSetupCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const code = new FormData(form).get("code")
    if (typeof code !== "string") return
    setSetupPending(true)
    setSetupError("")
    form.reset()
    try {
      await signInWithSetupCode(code.trim())
      await refreshSession()
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
        void client.invalidateQueries({ queryKey: sessionQuery.queryKey })
      }
      setSetupError(error instanceof ApiError && error.status === 429 ? "Try again later." : "Check your setup code.")
    } finally {
      setSetupPending(false)
    }
  }

  return (
    <main className="grid min-h-dvh justify-items-center px-5 pt-login-safe pb-10 md:place-items-center md:py-10">
      <div className="w-full max-w-110">
        <SwbIcon className="mb-6 size-14" />
        {loading ? (
          <DataState kind="loading" message={linkPending ? "Opening sign-in link…" : "Checking session…"} className="py-0" />
        ) : (
          <>
            <h1 className="font-display text-display-lg">{enroll ? "Add a passkey" : `Sign in to ${PRODUCT_NAME}`}</h1>
            {enroll ? (
              <form onSubmit={enrollPasskey} className="mt-6 space-y-4">
                {passkeysSupported && (
                  <Field label="Passkey name">
                    <Input value={name} onChange={(event) => setName(event.currentTarget.value)} placeholder="Passkey" />
                  </Field>
                )}
                {!passkeysSupported && <p className="text-body-sm text-muted-foreground">Passkeys require a compatible browser.</p>}
                {register.isError && <p role="alert" className="text-body-sm text-destructive">Try adding the passkey again.</p>}
                <div className="flex flex-wrap justify-end gap-3">
                  <Button variant="ghost" disabled={register.isPending} onClick={() => setEnrollmentDone(true)}>Skip for now</Button>
                  <Button type="submit" disabled={register.isPending || !passkeysSupported || !session.data?.csrf}>
                    <KeyRound aria-hidden />
                    {register.isPending ? "Adding…" : "Add a passkey"}
                  </Button>
                </div>
              </form>
            ) : (
              <div className="mt-4 space-y-4">
                {error && <p role="alert" className="text-body-sm text-destructive">{error}</p>}
                {session.isError ? (
                  <DataState kind="error" message="Session unavailable." onRetry={() => void session.refetch()} className="py-0" />
                ) : (
                  <>
                    {canSetUp && (
                      <form method="post" action="/auth/setup" onSubmit={enterSetupCode} className="space-y-4">
                        <Field label="Setup code">
                          <Input name="code" type="password" autoComplete="one-time-code" minLength={44} maxLength={64} required />
                        </Field>
                        {setupError && <p role="alert" className="text-body-sm text-destructive">{setupError}</p>}
                        <Button type="submit" disabled={setupPending}>{setupPending ? "Signing in…" : "Sign in"}</Button>
                      </form>
                    )}
                    {hasPasskey && !passkeysSupported && <p className="text-body-sm text-muted-foreground">Passkeys require a compatible browser.</p>}
                    {(hasPasskey && passkeysSupported || oidc) && (
                      <div className="flex flex-wrap items-center gap-2">
                        {hasPasskey && passkeysSupported && (
                          <Button disabled={signIn.isPending} onClick={() => signIn.mutate()}>
                            <KeyRound aria-hidden />
                            {signIn.isPending ? "Signing in…" : "Sign in with a passkey"}
                          </Button>
                        )}
                        {oidc && (
                          <Button asChild variant={hasPasskey && passkeysSupported ? "ghost" : "default"}>
                            <a href={oidcStart(next)}>Continue with {oidc.name}</a>
                          </Button>
                        )}
                      </div>
                    )}
                    {!canSetUp && !linkError && (error || (!oidc && (!hasPasskey || !passkeysSupported))) && (
                      <p className="text-body-sm text-muted-foreground">Ask your agent for a sign-in link.</p>
                    )}
                  </>
                )}
              </div>
            )}
          </>
        )}
        <div className="mt-6 border-t pt-4 font-mono text-mono-sm text-muted-foreground">{location.host}</div>
      </div>
    </main>
  )
}
