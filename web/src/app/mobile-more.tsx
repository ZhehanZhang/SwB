import { useRef } from "react"
import { useQuery } from "@tanstack/react-query"
import { FolderKanban, Menu } from "lucide-react"
import { Dialog } from "radix-ui"
import { Link, NavLink, useLocation } from "react-router-dom"
import { isSection, sections } from "@/app/navigation"
import { preloadOnTouch } from "@/app/route-pages"
import { boardQuery } from "@/pages/overview/data"

const projectsPath = "/#projects"
const rowClass = "console-mobile-tab console-pressable flex min-h-[52px] w-full touch-manipulation items-center gap-3 rounded-md px-3 text-caption text-muted-foreground"
const entries = sections.slice(3)

export function MobileMore({ open, onOpenChange, tabClass, iconClass, tabVisual }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  tabClass: string
  iconClass: string
  tabVisual: string
}) {
  const { pathname, hash } = useLocation()
  const { data: board } = useQuery({ ...boardQuery, enabled: open, staleTime: 30_000 })
  const showProjects = Boolean(board?.projects.length)
  const projectsSelected = isSection(pathname, "/projects") || (pathname === "/" && hash === "#projects")
  const active = open || projectsSelected || entries.some(({ to }) => isSection(pathname, to))
  const swipe = useRef<{ x: number; y: number } | null>(null)

  return (
    <Dialog.Root modal={false} open={open} onOpenChange={onOpenChange}>
      <Dialog.Trigger asChild>
        <button type="button" className={tabClass} data-selected={active || undefined}>
          <span className={tabVisual}><Menu aria-hidden className={iconClass} /><span>More</span></span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <div aria-hidden data-more-scrim data-state={open ? "open" : "closed"} className="console-more-scrim fixed inset-0 z-30 touch-none bg-overlay/50 md:hidden"
          onClick={() => onOpenChange(false)} />
        <Dialog.Content aria-describedby={undefined} inert={!open} className="console-more-sheet fixed inset-x-0 z-40 flex max-h-[75dvh] flex-col overflow-hidden rounded-t-xl border-t bg-surface px-5 pb-2 text-foreground md:hidden"
          onInteractOutside={event => event.preventDefault()}
          onTouchStart={event => {
            const touch = event.touches[0]
            swipe.current = event.touches.length === 1 && touch && event.currentTarget.querySelector("ul")?.scrollTop === 0
              ? { x: touch.clientX, y: touch.clientY } : null
          }}
          onTouchCancel={() => { swipe.current = null }}
          onTouchEnd={event => {
            const start = swipe.current
            const touch = event.changedTouches[0]
            swipe.current = null
            if (start && touch && touch.clientY - start.y > Math.max(48, Math.abs(touch.clientX - start.x))) {
              event.preventDefault()
              onOpenChange(false)
            }
          }}>
          <Dialog.Title className="sr-only">More</Dialog.Title>
          <Dialog.Close asChild>
            <button type="button" aria-label="Close More" className="mx-auto grid h-9 w-20 shrink-0 touch-none place-items-center rounded-md">
              <span className="h-1 w-9 rounded-full bg-border-strong" />
            </button>
          </Dialog.Close>
          <ul className="min-h-0 overflow-y-auto overscroll-contain">
            {showProjects && (
              <li>
                <Dialog.Close asChild>
                  <Link to={projectsPath} aria-current={projectsSelected ? "location" : undefined}
                    data-selected={projectsSelected || undefined} className={rowClass}
                    {...preloadOnTouch(projectsPath)}>
                    <FolderKanban aria-hidden className={iconClass} /><span>Projects</span>
                  </Link>
                </Dialog.Close>
              </li>
            )}
            {entries.map(({ name, to, icon: Icon }) => (
              <li key={to}>
                <Dialog.Close asChild>
                  <NavLink to={to} className={rowClass} {...preloadOnTouch(to)}>
                    <Icon aria-hidden className={iconClass} /><span>{name}</span>
                  </NavLink>
                </Dialog.Close>
              </li>
            ))}
          </ul>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
