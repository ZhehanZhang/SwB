import { Bot, Terminal } from "lucide-react"
import { useState } from "react"
import capy from "../../../src/switchboard/slack/avatars/capy.png?no-inline"
import chatgpt from "../../../src/switchboard/slack/avatars/chatgpt.png?no-inline"
import claudeCode from "../../../src/switchboard/slack/avatars/claude-code.png?no-inline"
import claude from "../../../src/switchboard/slack/avatars/claude.png?no-inline"
import codex from "../../../src/switchboard/slack/avatars/codex.png?no-inline"
import defaultAvatar from "../../../src/switchboard/slack/avatars/default.png?no-inline"
import dots from "../../../src/switchboard/slack/avatars/dots.png?no-inline"
import gemini from "../../../src/switchboard/slack/avatars/gemini.png?no-inline"
import grok from "../../../src/switchboard/slack/avatars/grok.png?no-inline"
import grokBot from "../../../src/switchboard/slack/avatars/grok-bot.png?no-inline"
import muse from "../../../src/switchboard/slack/avatars/muse.png?no-inline"
import swb from "../../../src/switchboard/slack/avatars/swb.png?no-inline"
import discord from "@/assets/discord.svg?no-inline"
import github from "@/assets/github.svg?no-inline"
import telegram from "@/assets/telegram.svg?no-inline"
import { connectorKind } from "@/lib/connector-kind"
import { cn } from "@/lib/utils"

const images: Record<string, string> = { discord, github, telegram }
const avatars: Record<string, string> = { capy, chatgpt, "claude-code": claudeCode, claude, codex, dots, gemini, grok, "grok-bot": grokBot, muse, swb }
const aliases = [
  ["grok-bot", "grok-bot"], ["grok-routine", "grok-bot"],
  ["claude-code", "claude-code"], ["claude", "claude"], ["anthropic", "claude"], ["grok", "grok"], ["xai", "grok"],
  ["openai", "chatgpt"], ["chatgpt", "chatgpt"], ["dots", "dots"], ["dot", "dots"], ["capy", "capy"], ["captain", "captain"],
  ["github", "github"], ["slack", "slack"], ["discord", "discord"], ["telegram", "telegram"],
  ["gemini", "gemini"], ["codex", "codex"], ["muse", "muse"], ["swb", "swb"], ["switchboard", "swb"], ["mcp", "mcp"],
] as const

const avatarPath = /^\/avatars\/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\.(png|jpe?g|gif)(?:\?v=[0-9]{1,20})?$/

function AvatarImage({ src, fallback, captain, adapt }: {
  src?: string; fallback?: string; captain: boolean; adapt?: boolean
}) {
  const [failed, setFailed] = useState(false)
  if (captain && (!src || failed)) return <span className="text-[length:clamp(14px,72cqi,24px)] leading-none font-bold">C</span>
  return <img alt="" src={failed ? fallback : src} onError={() => setFailed(true)}
    className={cn("size-full", captain ? "object-cover" : "object-contain", adapt && "[.dark_&]:invert [.dark_&]:hue-rotate-180")} />
}

export function LogoTile({ kind, className, avatarUrl }: { kind: string; className?: string; avatarUrl?: string | null }) {
  const id = kind.toLowerCase().replaceAll(/[_ ]/g, "-")
  const mark = connectorKind(id) ?? aliases.find(([prefix]) => id === prefix || id.startsWith(`${prefix}-`) || id.startsWith(`${prefix}:`))?.[1]
  const captain = mark === "captain" || mark === "capy" && avatarUrl !== undefined
  const bundled = mark && avatars[mark]
  const canonical = bundled ? `/avatars/${mark}.png` : undefined
  const avatar = typeof avatarUrl === "string" && avatarPath.exec(avatarUrl)?.[0] === avatarUrl
    ? avatarUrl : captain ? undefined : canonical
  const image = mark && images[mark]
  return (
    <span aria-hidden className={cn("inline-grid size-6 shrink-0 place-items-center overflow-hidden rounded-md border border-border bg-surface text-foreground [.dark_&]:border-border-strong [.dark_&]:bg-surface-sunken",
      captain && "border-logo-capy bg-logo-capy text-logo-capy-foreground [container-type:inline-size] [.dark_&]:bg-logo-capy", className)}>
      {captain || avatar ? <AvatarImage key={avatar} src={avatar} captain={captain}
          fallback={bundled ?? defaultAvatar}
          adapt={avatar === canonical && (mark === "codex" || mark === "gemini")} />
        : image && mark === "github" ? <span className="size-3/4 bg-foreground mask-contain mask-center mask-no-repeat" style={{ maskImage: `url("${image}")` }} />
        : image ? <img alt="" src={image} className="size-3/4 object-contain" />
        : mark === "slack" ? (
          <svg className="size-3/4" viewBox="0 0 24 24" fill="var(--logo-slack)">
            <path d="M5 15a2.5 2.5 0 1 1-2.5-2.5H5V15Zm1.25 0a2.5 2.5 0 0 1 5 0v6.25a2.5 2.5 0 1 1-5 0V15ZM9 5a2.5 2.5 0 1 1 2.5-2.5V5H9Zm0 1.25a2.5 2.5 0 0 1 0 5H2.75a2.5 2.5 0 1 1 0-5H9ZM19 9a2.5 2.5 0 1 1 2.5 2.5H19V9Zm-1.25 0a2.5 2.5 0 1 1-5 0V2.75a2.5 2.5 0 1 1 5 0V9ZM15 19a2.5 2.5 0 1 1-2.5 2.5V19H15Zm0-1.25a2.5 2.5 0 1 1 0-5h6.25a2.5 2.5 0 1 1 0 5H15Z" />
          </svg>
        ) : mark === "mcp" ? <Terminal className="size-3/4 stroke-[2.5]" /> : <Bot className="size-3/4 stroke-[2.5]" />}
    </span>
  )
}

export { LogoTile as Logo }
