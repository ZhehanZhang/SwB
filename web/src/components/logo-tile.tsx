import { Bot, Terminal } from "lucide-react"
import { useState } from "react"
import anthropic from "@/assets/anthropic.svg"
import claude from "@/assets/claude.svg"
import discord from "@/assets/discord.svg"
import gemini from "@/assets/gemini.svg"
import github from "@/assets/github.svg"
import grokRoutine from "@/assets/grok-routine.svg"
import openai from "@/assets/openai.svg"
import telegram from "@/assets/telegram.svg"
import xai from "@/assets/xai.svg"
import { connectorKind } from "@/lib/connector-kind"
import { cn } from "@/lib/utils"

const images: Record<string, string> = {
  anthropic, chatgpt: openai, "claude-code": claude, claude, codex: openai, discord, gemini, github, "grok-routine": grokRoutine,
  grok: xai, telegram, xai,
}
const darkTiles = new Set(["claude-code", "codex"])
const aliases = [
  ["claude-code", "claude-code"], ["claude", "claude"], ["grok-routine", "grok-routine"], ["grok", "grok"], ["xai", "xai"],
  ["openai", "chatgpt"], ["chatgpt", "chatgpt"], ["dot", "chatgpt"], ["capy", "capy"], ["captain", "capy"],
  ["github", "github"], ["slack", "slack"], ["discord", "discord"], ["telegram", "telegram"],
  ["gemini", "gemini"], ["codex", "codex"], ["anthropic", "anthropic"], ["mcp", "mcp"],
] as const

const avatarPath = /^\/avatars\/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.(png|jpe?g|gif)(?:\?v=[0-9]{1,20})?$/

function CaptainTile({ src }: { src?: string }) {
  const [failed, setFailed] = useState(false)
  return src && !failed ? <img alt="" src={src} className="size-full object-cover" onError={() => setFailed(true)} />
    : <span className="text-[16px] leading-none font-bold">C</span>
}

export function LogoTile({ kind, className, avatarUrl }: { kind: string; className?: string; avatarUrl?: string | null }) {
  const id = kind.toLowerCase().replaceAll("_", "-")
  const mark = connectorKind(id) ?? aliases.find(([prefix]) => id === prefix || id.startsWith(`${prefix}-`) || id.startsWith(`${prefix}:`))?.[1]
  const image = mark && images[mark]
  const mono = mark === "chatgpt" || mark === "github" || mark === "anthropic" || mark === "grok" || mark === "xai"
  const captainImage = mark === "capy" && typeof avatarUrl === "string" && avatarPath.exec(avatarUrl)?.[0] === avatarUrl
    ? avatarUrl : undefined
  return (
    <span aria-hidden className={cn("inline-grid size-6 shrink-0 place-items-center overflow-hidden rounded-md border border-border bg-surface text-foreground",
      mark && darkTiles.has(mark) && "border-socket bg-socket",
      mark === "capy" && "border-logo-capy bg-logo-capy text-logo-capy-foreground",
      mark === "grok-routine" && "border-0", className)}>
      {image && mono ? <span className="size-4 bg-foreground mask-contain mask-center mask-no-repeat" style={{ maskImage: `url("${image}")` }} />
        : image ? <img alt="" src={image} className={cn(mark === "grok-routine" ? "size-full" : "size-4", "object-contain", mark === "codex" && "invert")} />
        : mark === "muse" ? <svg className="size-4" viewBox="0 0 24 24" aria-hidden="true"><use href="/assets/claim-connectors.svg#muse" /></svg>
        : mark === "capy" ? <CaptainTile key={captainImage} src={captainImage} />
        : mark === "slack" ? (
          <svg className="size-[17px]" viewBox="0 0 24 24" fill="var(--logo-slack)">
            <path d="M5 15a2.5 2.5 0 1 1-2.5-2.5H5V15Zm1.25 0a2.5 2.5 0 0 1 5 0v6.25a2.5 2.5 0 1 1-5 0V15ZM9 5a2.5 2.5 0 1 1 2.5-2.5V5H9Zm0 1.25a2.5 2.5 0 0 1 0 5H2.75a2.5 2.5 0 1 1 0-5H9ZM19 9a2.5 2.5 0 1 1 2.5 2.5H19V9Zm-1.25 0a2.5 2.5 0 1 1-5 0V2.75a2.5 2.5 0 1 1 5 0V9ZM15 19a2.5 2.5 0 1 1-2.5 2.5V19H15Zm0-1.25a2.5 2.5 0 1 1 0-5h6.25a2.5 2.5 0 1 1 0 5H15Z" />
          </svg>
        ) : mark === "mcp" ? <Terminal className="size-4 stroke-[2.5]" /> : <Bot className="size-4 stroke-[2.5]" />}
    </span>
  )
}

export { LogoTile as Logo }
