import { Bot, Terminal } from "lucide-react"
import anthropic from "@/assets/anthropic.svg"
import chatgpt from "@/assets/chatgpt.png"
import claudeCode from "@/assets/claude-code.png"
import claude from "@/assets/claude.png"
import codex from "@/assets/codex.png"
import discord from "@/assets/discord.svg"
import gemini from "@/assets/gemini.png"
import github from "@/assets/github.svg"
import grok from "@/assets/grok.svg"
import telegram from "@/assets/telegram.svg"
import xai from "@/assets/xai.svg"
import { cn } from "@/lib/utils"

const images: Record<string, string> = {
  anthropic, chatgpt, "claude-code": claudeCode, claude, codex, discord, gemini, github, grok, telegram, xai,
}
const fullBleed = new Set(["chatgpt", "claude", "claude-code"])
const aliases = [
  ["claude-code", "claude-code"], ["claude", "claude"], ["grok", "grok"], ["xai", "xai"],
  ["openai", "chatgpt"], ["chatgpt", "chatgpt"], ["dot", "chatgpt"], ["capy", "capy"], ["captain", "capy"],
  ["github", "github"], ["slack", "slack"], ["discord", "discord"], ["telegram", "telegram"],
  ["gemini", "gemini"], ["codex", "codex"], ["anthropic", "anthropic"], ["mcp", "mcp"],
] as const

export function LogoTile({ kind, className }: { kind: string; className?: string }) {
  const id = kind.toLowerCase().replaceAll("_", "-")
  const mark = aliases.find(([prefix]) => id === prefix || id.startsWith(`${prefix}-`) || id.startsWith(`${prefix}:`))?.[1]
  const image = mark && images[mark]
  return (
    <span aria-hidden className={cn("inline-grid size-6 shrink-0 place-items-center overflow-hidden rounded-md border border-border bg-surface text-foreground", mark === "capy" && "border-[#73563e] bg-[#73563e] text-white", className)}>
      {mark && image ? <img alt="" src={image} className={fullBleed.has(mark) ? "size-full object-cover" : "size-4 object-contain"} />
        : mark === "capy" ? <span className="text-[16px] leading-none font-bold">C</span>
        : mark === "slack" ? (
          <svg className="size-[17px]" viewBox="0 0 24 24" fill="#4a154b">
            <path d="M5 15a2.5 2.5 0 1 1-2.5-2.5H5V15Zm1.25 0a2.5 2.5 0 0 1 5 0v6.25a2.5 2.5 0 1 1-5 0V15ZM9 5a2.5 2.5 0 1 1 2.5-2.5V5H9Zm0 1.25a2.5 2.5 0 0 1 0 5H2.75a2.5 2.5 0 1 1 0-5H9ZM19 9a2.5 2.5 0 1 1 2.5 2.5H19V9Zm-1.25 0a2.5 2.5 0 1 1-5 0V2.75a2.5 2.5 0 1 1 5 0V9ZM15 19a2.5 2.5 0 1 1-2.5 2.5V19H15Zm0-1.25a2.5 2.5 0 1 1 0-5h6.25a2.5 2.5 0 1 1 0 5H15Z" />
          </svg>
        ) : mark === "mcp" ? <Terminal className="size-4" /> : <Bot className="size-4" />}
    </span>
  )
}

export { LogoTile as Logo }
