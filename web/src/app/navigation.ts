import { Bot, Brain, Inbox, Info, KeyRound, ScrollText, Settings } from "lucide-react"

export const primarySections = [
  { name: "Agents", to: "/agents", icon: Bot },
  { name: "Tokens", to: "/tokens", icon: KeyRound },
  { name: "Inbox", to: "/inbox", icon: Inbox },
  { name: "Memory", to: "/memory", icon: Brain },
  { name: "Audit", to: "/audit", icon: ScrollText },
] as const

export const utilitySections = [
  { name: "About", to: "/about", icon: Info },
  { name: "Settings", to: "/settings", icon: Settings },
] as const

export const sections = [...primarySections, ...utilitySections] as const

export function isSection(pathname: string, to: string) {
  return pathname === to || pathname.startsWith(`${to}/`)
}
