import { useEffect, useState } from "react"
import { ExternalLink } from "lucide-react"
import { DataState } from "@/components/data-state"
import { Button } from "@/components/ui/button"

function validClaimUrl(value: string) {
  try {
    const url = new URL(value, window.location.origin)
    const localHttp = url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    if (url.protocol !== "https:" && !localHttp) return null
    if (!/^\/(?:claim|routines\/claim)\/[A-Za-z0-9_-]+\/?$/.test(url.pathname) || url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}

function duration(seconds: number) {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor(seconds / 60) % 60
  const remainder = String(seconds % 60).padStart(2, "0")
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${remainder}` : `${minutes}:${remainder}`
}

type ClaimLinkProps = { url: string; expiresAt: string }

export function ClaimLink({ url, expiresAt }: ClaimLinkProps) {
  const expiry = Date.parse(expiresAt)
  const href = validClaimUrl(url)
  const [now, setNow] = useState(Date.now)
  const seconds = Math.max(0, Math.ceil((expiry - now) / 1000))

  useEffect(() => {
    if (!href || !Number.isFinite(expiry) || seconds === 0) return
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, expiry - (seconds - 1) * 1000 - Date.now()))
    return () => window.clearTimeout(timer)
  }, [href, expiry, seconds])

  if (!href || !Number.isFinite(expiry)) {
    return <DataState kind="error" message="Claim link unavailable." />
  }

  return (
    <div className="flex flex-col items-start gap-2 border-b py-3">
      {seconds > 0 ? (
        <>
          <code className="max-w-full font-mono text-xs [overflow-wrap:anywhere]">{href}</code>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="font-mono text-xs text-muted-foreground" role="timer">Expires in {duration(seconds)}</span>
            <Button asChild variant="outline">
              <a href={href} target="_blank" rel="noopener noreferrer">
                <ExternalLink aria-hidden /> Open and paste token<span className="sr-only"> (opens in a new tab)</span>
              </a>
            </Button>
          </div>
        </>
      ) : <span role="status" className="text-sm text-muted-foreground">Claim link expired.</span>}
    </div>
  )
}
