import { useQueries, useQuery } from "@tanstack/react-query"
import { Check, Copy } from "lucide-react"
import { toast } from "sonner"
import captain from "../../../../site/public/marks/captain.webp?no-inline"
import { selectCodingAgents, setupChoices, type SetupChoice } from "../../../../site/lib/setup-picker"
import { personalizeSwb, siteUrl } from "../../../../site/lib/urls"
import { connectionNames, type ConnectionPlatform } from "@/app/navigation"
import { LogoTile } from "@/components/logo-tile"
import { Button } from "@/components/ui/button"
import { isObject, requestJson } from "@/lib/api"
import { connectionQuery } from "@/lib/connections"
import type { Agent } from "./data"

const prompts = import.meta.glob("../../../../src/switchboard/prompts/setup/*.md", {
  eager: true, query: "?raw", import: "default",
}) as Record<string, string>
const connectionPlatforms = Object.keys(connectionNames) as ConnectionPlatform[]

function setupDocs(prompt: string, origin: string) {
  const locale = document.documentElement.lang
  const prefix = /^(zh-Hans|zh-Hant|es|fr|de|ko|ja)$/.test(locale) ? `/${locale}` : ""
  const path = prompt === "webhook" ? "/docs/blocks/webhooks" : `/docs/setup/${prompt}`
  return `${siteUrl}${prefix}${path}#swb=${encodeURIComponent(origin)}`
}

export function AgentSetup({ agents }: { agents: Agent[] }) {
  const origin = useQuery({
    queryKey: ["agent-setup-origin"],
    queryFn: async ({ signal }) => (await requestJson("/api/agents/setup",
      (value): value is { public_origin: string | null } => isObject(value)
        && (value.public_origin === null || typeof value.public_origin === "string"), { signal })).public_origin,
  })
  const connections = useQueries({ queries: connectionPlatforms.map(connectionQuery) })
  const publicOrigin = origin.data ?? location.origin
  const registered = new Set(agents.filter(agent => agent.type !== "pending").map(agent =>
    agent.type === "captain" ? "capy-captain" : agent.type === "dot" ? "dots" :
      agent.type === "routine" && agent.logo === "claude" ? "claude-project" : agent.logo))
  for (const connection of connections) {
    if (connection.data?.state === "connected"
      && (connection.data.platform !== "webhook" || connection.data.details?.webhooks.length)) registered.add(connection.data.platform)
  }

  async function copy(id: SetupChoice, name: string) {
    const text = prompts[`../../../../src/switchboard/prompts/setup/${name}.md`]
    if (!text) return
    try {
      const prompt = name === "coding-agent" ? selectCodingAgents(text, [id]) : text
      await navigator.clipboard.writeText(personalizeSwb(prompt.trim(), publicOrigin))
      toast.success("Prompt copied")
    } catch {
      toast.error("Copy failed")
    }
  }

  return (
    <section className="border-b pb-4" aria-labelledby="connect-agent">
      <h2 id="connect-agent" className="text-heading-md">Connect an agent</h2>
      <ul className="mt-3 grid grid-cols-2 gap-x-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {setupChoices.map(({ id, mark, name, prompt }) => (
          <li key={id} className="flex min-w-0 flex-col justify-between gap-2 border-b py-2.5">
            <div className="flex min-w-0 items-center gap-2 text-label-sm">
              {id === "capy-captain" ? <img src={captain} alt="" className="size-6 shrink-0 rounded-md object-cover" /> : <LogoTile kind={mark} />}
              <span className="wrap-anywhere">{name}</span>
              {(registered.has(id) || id === "gemini-cli" && registered.has("gemini")
                || id === "grok-bot" && registered.has("grok-routine")) &&
                <span className="ms-auto text-muted-foreground" title="Registered"><Check aria-label="Registered" role="img" className="size-4" /></span>}
            </div>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Button variant="outline" size="sm" className="min-h-8 px-2" aria-label={`Copy ${name} setup prompt`}
                disabled={origin.isPending} onClick={() => void copy(id, prompt)}><Copy aria-hidden /> Copy prompt</Button>
              <a href={setupDocs(prompt, publicOrigin)} aria-label={`${name} setup guide`}
                className="text-caption text-muted-foreground underline-offset-2 hover:underline" target="_blank" rel="noreferrer">Guide</a>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
