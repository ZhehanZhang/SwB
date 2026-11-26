import { Activity, Blocks, Folder, Inbox, Network, SquareTerminal, Users } from "lucide-react"

export const sections = [
  { name: "Canvas", to: "/canvas", icon: Network },
  { name: "Agents", to: "/agents", icon: Users },
  { name: "Inbox", to: "/inbox", icon: Inbox },
  { name: "Provision", to: "/provision", icon: Blocks },
  { name: "Projects", to: "/projects", icon: Folder },
  { name: "Memory", to: "/memory", icon: SquareTerminal },
  { name: "Audit", to: "/audit", icon: Activity },
] as const
