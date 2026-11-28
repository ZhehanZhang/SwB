import { createContext, useContext, type ReactNode } from "react"
import { createPortal } from "react-dom"

export const PageHeaderActionsTarget = createContext<HTMLDivElement | null>(null)

export function PageHeaderActions({ children }: { children: ReactNode }) {
  const target = useContext(PageHeaderActionsTarget)
  return target && createPortal(children, target)
}
