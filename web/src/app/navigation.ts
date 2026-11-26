import { Bot, Brain, Inbox, KeyRound, ScrollText, Settings } from "lucide-react"

export const sections = [
  { name: "Agents", to: "/agents", icon: Bot },
  { name: "Tokens", to: "/tokens", icon: KeyRound },
  { name: "Inbox", to: "/inbox", icon: Inbox },
  { name: "Memory", to: "/memory", icon: Brain },
  { name: "Audit", to: "/audit", icon: ScrollText },
  { name: "Settings", to: "/settings", icon: Settings },
] as const

export function isSection(pathname: string, to: string) {
  return pathname === to || pathname.startsWith(`${to}/`)
}
