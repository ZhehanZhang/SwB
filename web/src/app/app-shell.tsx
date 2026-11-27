import { useEffect, useState } from "react"
import { ChevronLeft, Menu } from "lucide-react"
import { Link, NavLink, Outlet, useLocation } from "react-router-dom"
import { PRODUCT_NAME } from "@/app/brand"
import { CommandMenu } from "@/app/header-controls"
import { PageTitle } from "@/app/page-title"
import { isSection, primarySections, sections, utilitySections } from "@/app/navigation"
import { SwbIcon } from "@/components/swb-icon"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const tabClass = "flex min-w-0 flex-col items-center justify-center gap-1 rounded-md text-caption text-muted-foreground hover:text-foreground"
const mobileTabClass = "console-mobile-tab flex h-14 min-w-0 touch-manipulation flex-col items-center justify-center gap-1 rounded-md text-caption text-muted-foreground"
const mobileIconClass = "size-6 shrink-0"

function HomeNav({ active }: { active: boolean }) {
  return (
    <Link to="/" aria-label={`${PRODUCT_NAME} overview`} aria-current={active ? "page" : undefined}
      className={cn(tabClass, "h-[72px] w-16")}
    >
      <SwbIcon muted={!active} className={active ? "size-12" : "size-7"} />
      {!active && <span>Overview</span>}
    </Link>
  )
}

function SectionNav({ section, selected = false }: { section: typeof sections[number]; selected?: boolean }) {
  const { name, to, icon: Icon } = section
  return (
    <NavLink to={to} className={({ isActive }) => cn(tabClass, "h-14 w-16 hover:bg-muted",
      (isActive || selected) && "bg-muted text-foreground")}>
      <Icon aria-hidden className="size-5" /> {name}
    </NavLink>
  )
}

function MobileNavigation({ pathname, home }: { pathname: string; home: boolean }) {
  const more = sections.slice(3)
  const active = more.some(({ to }) => isSection(pathname, to))
  return (
    <nav aria-label="Navigation" className="console-mobile-nav pointer-events-none fixed inset-x-0 bottom-0 z-30 h-[calc(var(--mobile-tabbar-height)+env(safe-area-inset-bottom))] px-5 md:hidden">
      <div className="pointer-events-auto grid grid-cols-5">
        <Link to="/" aria-current={home ? "page" : undefined} className={mobileTabClass}>
          <SwbIcon tab className={mobileIconClass} /><span>Overview</span>
        </Link>
        {sections.slice(0, 3).map(({ name, to, icon: Icon }) => (
          <NavLink key={to} to={to} className={mobileTabClass}>
            <Icon aria-hidden className={mobileIconClass} /><span>{name}</span>
          </NavLink>
        ))}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" className={mobileTabClass} data-selected={active || undefined}>
              <Menu aria-hidden className={mobileIconClass} /><span>More</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="end" collisionPadding={12}>
            {more.map(({ name, to, icon: Icon }) => (
              <DropdownMenuItem key={to} asChild><NavLink to={to}><Icon aria-hidden /> {name}</NavLink></DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  )
}

export function AppShell() {
  const { pathname } = useLocation()
  const project = isSection(pathname, "/projects")
  const home = pathname === "/" || project || isSection(pathname, "/canvas")
  const [title, setTitle] = useState<string>()
  const heading = title ?? (home ? PRODUCT_NAME : sections.find(({ to }) => isSection(pathname, to))?.name ?? PRODUCT_NAME)
  useEffect(() => { document.title = heading === PRODUCT_NAME ? heading : `${heading} | ${PRODUCT_NAME}` }, [heading])

  return (
    <PageTitle.Provider value={setTitle}>
      <a className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2" href="#main-content">Skip to content</a>
      <nav aria-label="Navigation" className="fixed inset-y-0 left-0 z-20 hidden w-[var(--rail-width)] flex-col gap-1 px-2 pt-4 pb-4 pl-4 md:flex">
        <HomeNav active={home} />
        {primarySections.map(section => <SectionNav key={section.to} section={section} />)}
        <div className="flex-1" />
        {utilitySections.map(section => <SectionNav key={section.to} section={section} selected={isSection(pathname, "/about")} />)}
      </nav>
      <div className="min-h-dvh px-5 pt-[max(28px,env(safe-area-inset-top))] pb-[calc(var(--mobile-tabbar-clearance)+env(safe-area-inset-bottom))] md:ml-[var(--rail-width)] md:px-8 md:pt-4 md:pb-8">
        <header className="flex h-[var(--topbar-height)] min-w-0 items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-2">
            {project && <Link to="/" aria-label={`Back to ${PRODUCT_NAME}`} className="grid size-5 shrink-0 place-items-center text-muted-foreground md:hidden"><ChevronLeft aria-hidden className="size-5" /></Link>}
            <h1 className="truncate text-page-title">{heading}</h1>
          </div>
          <CommandMenu />
        </header>
        <main id="main-content" tabIndex={-1} className="min-w-0">
          <Outlet />
        </main>
      </div>
      <MobileNavigation pathname={pathname} home={home} />
    </PageTitle.Provider>
  )
}
