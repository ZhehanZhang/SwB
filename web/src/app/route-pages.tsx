import { lazy, type ComponentType } from "react"
import { isSection } from "@/app/navigation"

function splitPage(importPage: () => Promise<{ default: ComponentType }>) {
  let chunk: ReturnType<typeof importPage> | undefined
  function preload() {
    return chunk ??= importPage().catch((error: unknown) => {
      chunk = undefined
      throw error
    })
  }
  return { Page: lazy(preload), preload }
}

const agents = splitPage(() => import("@/pages/agents").then(({ AgentsPage }) => ({ default: AgentsPage })))
const inbox = splitPage(() => import("@/pages/inbox").then(({ InboxPage }) => ({ default: InboxPage })))
const memory = splitPage(() => import("@/pages/memory").then(({ MemoryPage }) => ({ default: MemoryPage })))
const audit = splitPage(() => import("@/pages/audit").then(({ AuditPage }) => ({ default: AuditPage })))
const overview = splitPage(() => import("@/pages/overview").then(({ OverviewPage }) => ({ default: OverviewPage })))
const project = splitPage(() => import("@/pages/project").then(({ ProjectPage }) => ({ default: ProjectPage })))
const tokens = splitPage(() => import("@/pages/tokens").then(({ TokensPage }) => ({ default: TokensPage })))
const settings = splitPage(() => import("@/pages/settings").then(({ SettingsPage }) => ({ default: SettingsPage })))
const about = splitPage(() => import("@/pages/about").then(({ AboutPage }) => ({ default: AboutPage })))

export const {
  AgentsPage, InboxPage, MemoryPage, AuditPage, OverviewPage, ProjectPage, TokensPage, SettingsPage, AboutPage,
} = {
  AgentsPage: agents.Page, InboxPage: inbox.Page, MemoryPage: memory.Page, AuditPage: audit.Page,
  OverviewPage: overview.Page, ProjectPage: project.Page, TokensPage: tokens.Page,
  SettingsPage: settings.Page, AboutPage: about.Page,
}

const pages = [
  ["/", overview], ["/agents", agents], ["/tokens", tokens], ["/inbox", inbox],
  ["/projects", project], ["/memory", memory], ["/audit", audit], ["/settings", settings], ["/about", about],
] as const

export function preloadRoute(to: string) {
  const pathname = to.split("#", 1)[0]
  const route = pages.find(([prefix]) => isSection(pathname, prefix))
  return route?.[1].preload().catch(() => undefined)
}

export function preloadOnTouch(to: string) {
  const preload = () => { void preloadRoute(to) }
  return { onPointerDown: preload, onTouchStart: preload, onFocus: preload }
}

export function preloadOnIdle() {
  const routes = ["/", "/agents", "/tokens", "/inbox"]
  const supportsIdle = typeof window.requestIdleCallback === "function"
  let stopped = false
  let handle: number
  function next() {
    const to = routes.shift()
    if (stopped || to === undefined) return
    const preload = () => { void preloadRoute(to)?.finally(next) }
    handle = supportsIdle ? window.requestIdleCallback(preload) : window.setTimeout(preload, 120)
  }
  next()
  return () => {
    stopped = true
    if (supportsIdle) window.cancelIdleCallback(handle)
    else window.clearTimeout(handle)
  }
}
