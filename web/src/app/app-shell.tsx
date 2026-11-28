import { Suspense, useEffect, useRef, useState, ViewTransition, type MouseEvent, type PointerEvent } from "react"
import { ChevronLeft } from "lucide-react"
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { PRODUCT_NAME } from "@/app/brand"
import { CommandMenu } from "@/app/header-controls"
import { MobileMore } from "@/app/mobile-more"
import { PageHeaderActionsTarget } from "@/app/page-header-actions"
import { PageTitle } from "@/app/page-title"
import { connectionNames, isConnectionPlatform, isSection, primarySections, sections, utilitySections } from "@/app/navigation"
import { preloadOnIdle, preloadOnTouch } from "@/app/route-pages"
import { SwbIcon } from "@/components/swb-icon"
import "./console-navigation.css"
import "./mobile-navigation.css"

const tabClass = "console-pressable flex min-w-0 flex-col items-center justify-center rounded-md text-muted-foreground"
const railTabClass = `${tabClass} w-16 gap-0.5 text-nav-label`
const mobileTabClass = `console-mobile-hit console-mobile-tab ${tabClass} h-14 gap-1 text-caption touch-manipulation`
const mobileTabVisual = "flex flex-col items-center gap-1"
const mobileIconClass = "size-5 shrink-0"

function HomeNav({ active }: { active: boolean }) {
  return (
    <Link to="/" aria-label={`${PRODUCT_NAME} overview`} aria-current={active ? "page" : undefined}
      className={`${railTabClass} console-rail-overview relative h-18`}
    >
      <span className="console-rail-overview-label absolute inset-x-0 bottom-3.25 text-center whitespace-nowrap">Overview</span>
    </Link>
  )
}

function SectionNav({ section, selected = false }: { section: typeof sections[number]; selected?: boolean }) {
  const { name, to, icon: Icon } = section
  return (
    <NavLink to={to} data-selected={selected || undefined} className={`${railTabClass} console-mobile-tab h-14`}>
      <Icon aria-hidden className="size-5 shrink-0" /><span className="whitespace-nowrap">{name}</span>
    </NavLink>
  )
}

function MobileNavigation({ home, moreOpen, setMoreOpen }: { home: boolean; moreOpen: boolean; setMoreOpen: (open: boolean) => void }) {
  const navigate = useNavigate()
  const press = useRef<{ tab: HTMLElement; pointer: number; x: number; y: number } | null>(null)
  const activation = useRef<{ tab: HTMLElement; when: number } | null>(null)

  function findTab(target: EventTarget | null) {
    return target instanceof Element ? target.closest<HTMLElement>(".console-mobile-hit") : null
  }

  function startTouch(event: PointerEvent<HTMLElement>) {
    if (event.pointerType !== "touch" || !event.isPrimary) return
    const tab = findTab(event.target)
    press.current = tab ? { tab, pointer: event.pointerId, x: event.clientX, y: event.clientY } : null
  }

  function trackTouch(event: PointerEvent<HTMLElement>) {
    const start = press.current
    if (start?.pointer === event.pointerId && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 12) {
      press.current = null
    }
  }

  function finishTouch(event: PointerEvent<HTMLElement>) {
    const start = press.current
    if (!start || start.pointer !== event.pointerId) return
    press.current = null
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > 12
      || findTab(document.elementFromPoint(event.clientX, event.clientY)) !== start.tab) return
    activation.current = { tab: start.tab, when: event.timeStamp }
    if (start.tab instanceof HTMLAnchorElement) {
      setMoreOpen(false)
      void navigate(start.tab.pathname)
    } else {
      setMoreOpen(!moreOpen)
    }
  }

  function suppressDuplicateClick(event: MouseEvent<HTMLElement>) {
    const last = activation.current
    const pointerType = "pointerType" in event.nativeEvent ? event.nativeEvent.pointerType : null
    if (!last || findTab(event.target) !== last.tab || event.timeStamp - last.when > 700
      || pointerType === "mouse" || (!pointerType && event.detail === 0)) return
    activation.current = null
    event.preventDefault()
    event.stopPropagation()
  }

  return (
    <nav aria-label="Navigation" data-more-open={moreOpen || undefined}
      className="console-mobile-nav pointer-events-none fixed inset-x-0 bottom-0 z-45 h-mobile-tabbar px-5 md:hidden"
      onPointerDownCapture={startTouch} onPointerMoveCapture={trackTouch} onPointerUpCapture={finishTouch}
      onPointerCancelCapture={() => { press.current = null }} onClickCapture={suppressDuplicateClick}>
      <div className="pointer-events-auto grid grid-cols-5">
        <Link to="/" aria-current={home ? "page" : undefined} className={mobileTabClass}
          onClick={() => setMoreOpen(false)} {...preloadOnTouch("/")}>
          <span className={mobileTabVisual}><span aria-hidden className={mobileIconClass} /><span>Overview</span></span>
        </Link>
        {sections.slice(0, 3).map(({ name, to, icon: Icon }) => (
          <NavLink key={to} to={to} className={mobileTabClass}
            onClick={() => setMoreOpen(false)} {...preloadOnTouch(to)}>
            <span className={mobileTabVisual}><Icon aria-hidden className={mobileIconClass} /><span>{name}</span></span>
          </NavLink>
        ))}
        <MobileMore open={moreOpen} onOpenChange={setMoreOpen} tabClass={mobileTabClass} iconClass={mobileIconClass} tabVisual={mobileTabVisual} />
      </div>
    </nav>
  )
}

export function AppShell() {
  const { pathname } = useLocation()
  const project = isSection(pathname, "/projects")
  const platform = isSection(pathname, "/connections") ? pathname.split("/")[2] : undefined
  const connection = isConnectionPlatform(platform)
  const home = pathname === "/" || project
  const [title, setTitle] = useState<string>()
  const [moreOpen, setMoreOpen] = useState(false)
  const [headerActions, setHeaderActions] = useState<HTMLDivElement | null>(null)
  const heading = connection ? connectionNames[platform] : title ?? (home ? PRODUCT_NAME : sections.find(({ to }) => isSection(pathname, to))?.name ?? PRODUCT_NAME)
  useEffect(() => { document.title = heading === PRODUCT_NAME ? heading : `${heading} | ${PRODUCT_NAME}` }, [heading])
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 48rem)")
    const closeMoreOnDesktop = () => { if (desktop.matches) setMoreOpen(false) }
    desktop.addEventListener("change", closeMoreOnDesktop)
    const cancelPreload = desktop.matches ? undefined : preloadOnIdle()
    return () => {
      desktop.removeEventListener("change", closeMoreOnDesktop)
      cancelPreload?.()
    }
  }, [])

  return (
    <PageTitle.Provider value={setTitle}>
      <a className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2" href="#main-content">Skip to content</a>
      <nav aria-label="Navigation" className="fixed inset-y-0 left-0 z-20 hidden w-rail flex-col gap-0.5 px-2 pt-4 pb-4 pl-4 md:flex">
        <HomeNav active={home} />
        {primarySections.map(section => <SectionNav key={section.to} section={section} />)}
        <div className="flex-1" />
        {utilitySections.map(section => <SectionNav key={section.to} section={section} selected={connection || isSection(pathname, "/about")} />)}
      </nav>
      <div inert={moreOpen} className="min-h-dvh px-5 pt-console-safe pb-mobile-tabbar md:ml-rail md:px-8 md:pt-4 md:pb-8">
        <header className="flex h-topbar min-w-0 items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-2">
            {project && <Link to="/" aria-label={`Back to ${PRODUCT_NAME}`} className="grid size-5 shrink-0 place-items-center text-muted-foreground md:hidden"><ChevronLeft aria-hidden className="size-5" /></Link>}
            {connection && <Link to="/settings" aria-label="Back to Settings" className="console-pressable console-press-scale grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground md:hidden"><ChevronLeft aria-hidden className="size-5" /></Link>}
            <h1 className="truncate text-page-title">{heading}</h1>
          </div>
          <div className="flex shrink-0 items-center gap-2 md:gap-4">
            <div ref={setHeaderActions} className="flex items-center empty:hidden" />
            <CommandMenu />
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="min-w-0">
          <Suspense fallback={<div className="min-h-72" aria-busy="true" />}>
            <ViewTransition key={pathname} name="console-route" default="none" share="console-route-fade">
              <div className="min-w-0">
                <PageHeaderActionsTarget.Provider value={headerActions}><Outlet /></PageHeaderActionsTarget.Provider>
              </div>
            </ViewTransition>
          </Suspense>
        </main>
      </div>
      <MobileNavigation home={home} moreOpen={moreOpen} setMoreOpen={setMoreOpen} />
      <div aria-hidden data-home={home || undefined} className="console-overview-mark pointer-events-none fixed top-0 left-0">
        <SwbIcon className="block" />
      </div>
    </PageTitle.Provider>
  )
}
