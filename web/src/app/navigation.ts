import { Bot, Brain, Inbox, Info, KeyRound, ScrollText, Settings } from "lucide-react"

export const primarySections = [
  { name: "Agents", to: "/agents", icon: Bot },
  { name: "Tokens", to: "/tokens", icon: KeyRound },
  { name: "Inbox", to: "/inbox", icon: Inbox },
  { name: "Memory", to: "/memory", icon: Brain },
  { name: "Audit", to: "/audit", icon: ScrollText },
] as const

export const utilitySections = [
  { name: "Settings", to: "/settings", icon: Settings },
] as const

export const sections = [...primarySections, ...utilitySections, { name: "About", to: "/about", icon: Info }] as const

export const connectionNames = { slack: "Slack", discord: "Discord", telegram: "Telegram", github: "GitHub", webhook: "Webhooks" } as const
export type ConnectionPlatform = keyof typeof connectionNames

export function isConnectionPlatform(value: string | undefined): value is ConnectionPlatform {
  return value !== undefined && Object.hasOwn(connectionNames, value)
}

export function isSection(pathname: string, to: string) {
  return pathname === to || pathname.startsWith(`${to}/`)
}
