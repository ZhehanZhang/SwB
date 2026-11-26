import { Ellipsis } from "lucide-react"
import { NavLink, Outlet, useLocation } from "react-router-dom"
import { AccountMenu, CommandMenu, HealthControl } from "@/app/header-controls"
import { sections } from "@/app/navigation"
import { Footer } from "@/components/footer"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const bottomTabs = sections.slice(0, 4)
const moreTabs = sections.slice(4)

function MobileNavigation() {
  const { pathname } = useLocation()
  const moreActive = moreTabs.some(({ to }) => pathname === to || pathname.startsWith(`${to}/`))

  return (
    <nav aria-label="Navigation" className="fixed inset-x-0 bottom-0 z-30 border-t bg-card pb-[env(safe-area-inset-bottom)] md:hidden">
      <div className="grid h-14 grid-cols-5">
        {bottomTabs.map(({ name, to, icon: Icon }) => (
          <NavLink
            key={to}
            to={to === "/canvas" && pathname === "/" ? "/" : to}
            className={({ isActive }) => cn(
              "flex min-w-0 flex-col items-center justify-center gap-0.5 border-t-2 border-transparent text-[11px] font-medium text-muted-foreground focus-visible:outline-offset-[-4px]",
              isActive && "border-lamp text-foreground",
            )}
          >
            <Icon aria-hidden className="size-[18px]" /> {name}
          </NavLink>
        ))}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className={cn(
                "h-full min-w-0 flex-col gap-0.5 rounded-none border-t-2 border-transparent px-0 text-[11px] text-muted-foreground focus-visible:outline-offset-[-4px] [&_svg]:size-[18px]",
                moreActive && "border-lamp text-foreground",
              )}
            >
              <Ellipsis aria-hidden /> More
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="end" collisionPadding={8}>
            {moreTabs.map(({ name, to, icon: Icon }) => (
              <DropdownMenuItem key={to} asChild>
                <NavLink to={to}><Icon aria-hidden /> {name}</NavLink>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  )
}

export function AppShell() {
  const { pathname } = useLocation()

  return (
    <div className="flex min-h-dvh flex-col pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0">
      <a
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-card focus:px-3 focus:py-2"
        href="#main-content"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-console-line bg-console pt-[env(safe-area-inset-top)] text-console-foreground">
        <div className="flex min-h-14 items-center gap-2 px-3 lg:gap-3 lg:px-4 xl:px-6">
          <NavLink
            to="/"
            end
            aria-label="Switchboard home"
            className="inline-flex shrink-0 items-center gap-2 rounded-sm font-display text-xl leading-none font-extrabold tracking-tight focus-visible:outline-lamp"
          >
            <span aria-hidden className="grid size-[18px] place-items-center rounded-full bg-lamp">
              <span className="size-1.5 rounded-full bg-console" />
            </span>
            Switchboard
          </NavLink>
          <nav aria-label="Navigation" className="hidden min-w-0 flex-1 self-stretch md:flex">
            {sections.map(({ name, to }) => (
              <NavLink
                key={to}
                to={to === "/canvas" && pathname === "/" ? "/" : to}
                className={({ isActive }) => cn(
                  "inline-flex items-center border-b-2 border-transparent px-2 pt-0.5 text-sm font-medium whitespace-nowrap text-console-muted hover:text-console-foreground focus-visible:outline-offset-[-4px] focus-visible:outline-lamp xl:px-3",
                  isActive && "border-lamp text-console-foreground",
                )}
              >
                {name}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center sm:gap-1">
            <HealthControl />
            <CommandMenu />
            <AccountMenu />
          </div>
        </div>
      </header>
      <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 scroll-mt-20 px-4 py-6 sm:px-6">
        <Outlet />
      </main>
      <Footer />
      <MobileNavigation />
    </div>
  )
}
