import { Bot, Terminal } from "lucide-react"
import { useState } from "react"
import discord from "@/assets/discord.svg"
import github from "@/assets/github.svg"
import telegram from "@/assets/telegram.svg"
import { connectorKind } from "@/lib/connector-kind"
import { cn } from "@/lib/utils"

const images: Record<string, string> = { discord, github, telegram }
const avatars = new Set(["claude-code", "claude", "chatgpt", "codex", "gemini", "grok", "muse", "swb", "capy"])
const aliases = [
  ["claude-code", "claude-code"], ["claude", "claude"], ["anthropic", "claude"], ["grok", "grok"], ["xai", "grok"],
  ["openai", "chatgpt"], ["chatgpt", "chatgpt"], ["dot", "chatgpt"], ["dots", "chatgpt"], ["capy", "capy"], ["captain", "capy"],
  ["github", "github"], ["slack", "slack"], ["discord", "discord"], ["telegram", "telegram"],
  ["gemini", "gemini"], ["codex", "codex"], ["muse", "muse"], ["swb", "swb"], ["switchboard", "swb"], ["mcp", "mcp"],
] as const

const avatarPath = /^\/avatars\/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.(png|jpe?g|gif)(?:\?v=[0-9]{1,20})?$/

function AvatarImage({ src, fallback }: { src: string; fallback: string }) {
  const [failed, setFailed] = useState(false)
  return <img alt="" src={failed ? fallback : src} className="size-full object-contain" onError={() => setFailed(true)} />
}

export function LogoTile({ kind, className, avatarUrl }: { kind: string; className?: string; avatarUrl?: string | null }) {
  const id = kind.toLowerCase().replaceAll(/[_ ]/g, "-")
  const mark = connectorKind(id) ?? aliases.find(([prefix]) => id === prefix || id.startsWith(`${prefix}-`) || id.startsWith(`${prefix}:`))?.[1]
  const fallback = mark && avatars.has(mark) ? `/avatars/${mark}.png` : undefined
  const avatar = typeof avatarUrl === "string" && avatarPath.test(avatarUrl) ? avatarUrl : fallback
  const image = mark && images[mark]
  return (
    <span aria-hidden className={cn("inline-grid size-6 shrink-0 place-items-center",
      !avatar && "overflow-hidden rounded-md border border-border bg-surface text-foreground", className)}>
      {avatar ? <AvatarImage key={avatar} src={avatar} fallback={fallback ?? "/avatars/default.png"} />
        : image && mark === "github" ? <span className="size-4 bg-foreground mask-contain mask-center mask-no-repeat" style={{ maskImage: `url("${image}")` }} />
        : image ? <img alt="" src={image} className="size-4 object-contain" />
        : mark === "slack" ? (
          <svg className="size-[17px]" viewBox="0 0 24 24" fill="var(--logo-slack)">
            <path d="M5 15a2.5 2.5 0 1 1-2.5-2.5H5V15Zm1.25 0a2.5 2.5 0 0 1 5 0v6.25a2.5 2.5 0 1 1-5 0V15ZM9 5a2.5 2.5 0 1 1 2.5-2.5V5H9Zm0 1.25a2.5 2.5 0 0 1 0 5H2.75a2.5 2.5 0 1 1 0-5H9ZM19 9a2.5 2.5 0 1 1 2.5 2.5H19V9Zm-1.25 0a2.5 2.5 0 1 1-5 0V2.75a2.5 2.5 0 1 1 5 0V9ZM15 19a2.5 2.5 0 1 1-2.5 2.5V19H15Zm0-1.25a2.5 2.5 0 1 1 0-5h6.25a2.5 2.5 0 1 1 0 5H15Z" />
          </svg>
        ) : mark === "mcp" ? <Terminal className="size-4 stroke-[2.5]" /> : <Bot className="size-4 stroke-[2.5]" />}
    </span>
  )
}

export { LogoTile as Logo }
