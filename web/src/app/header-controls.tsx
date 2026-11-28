import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Command } from "cmdk"
import { Command as CommandIcon, FolderKanban, Search } from "lucide-react"
import { Dialog } from "radix-ui"
import { useNavigate } from "react-router-dom"
import { PRODUCT_NAME } from "@/app/brand"
import { sections } from "@/app/navigation"
import { DataState } from "@/components/data-state"
import { SwbIcon } from "@/components/swb-icon"
import { Button } from "@/components/ui/button"
import { requestJson } from "@/lib/api"

type Project = { slug: string; name: string }

function isProjectList(value: unknown): value is { projects: Project[] } {
  if (typeof value !== "object" || value === null || !("projects" in value) || !Array.isArray(value.projects)) return false
  return value.projects.every(p => p !== null && typeof p === "object" && typeof p.slug === "string" && typeof p.name === "string")
}

const itemClass = "flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 text-body-sm select-none data-[selected=true]:bg-muted"
const isMac = navigator.userAgent.includes("Macintosh") && navigator.maxTouchPoints < 2
const keycapClass = "grid h-5 min-w-5 place-items-center rounded-sm border bg-background px-1 font-sans text-label-sm font-medium text-muted-foreground"

export function CommandMenu() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const projects = useQuery({
    queryKey: ["jump-projects"],
    queryFn: ({ signal }) => requestJson("/api/projects", isProjectList, { signal }),
    enabled: open,
    staleTime: 60_000,
  })

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.repeat || event.altKey || event.shiftKey) return
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return
      event.preventDefault()
      setOpen(value => !value)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  function jump(path: string) {
    setOpen(false)
    void navigate(path)
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button
          variant="outline"
          size="icon"
          aria-label="Search"
          aria-keyshortcuts="Meta+K Control+K"
          className="border-0 text-body-sm text-muted-foreground md:w-70 md:justify-start md:border md:px-3 [&>svg]:size-5 md:[&>svg]:size-4"
        >
          <Search aria-hidden />
          <span className="hidden md:inline">Search</span>
          <span aria-hidden className="ml-auto hidden items-center gap-0.5 md:inline-flex">
            <kbd className={keycapClass}>{isMac ? <CommandIcon aria-label="Command" className="size-3 stroke-2" strokeWidth={2} /> : "Ctrl"}</kbd>
            <kbd className={keycapClass}>K</kbd>
          </span>
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay/50" />
        <Dialog.Content aria-describedby={undefined} className="fixed inset-x-5 top-command-menu z-50 mx-auto max-w-lg overflow-hidden rounded-lg border bg-surface">
          <Dialog.Title className="sr-only">Search</Dialog.Title>
          <Command label="Search">
            <div className="flex items-center gap-2 border-b px-4">
              <Search aria-hidden className="size-4 text-muted-foreground" />
              <Command.Input placeholder="Search" autoFocus className="min-h-12 w-full bg-transparent text-body-md outline-none focus-visible:outline-none" />
            </div>
            <Command.List className="max-h-command-list overflow-y-auto p-2">
              <Command.Empty className="px-2"><DataState kind="empty" /></Command.Empty>
              <Command.Item value={`${PRODUCT_NAME} Overview Home`} onSelect={() => jump("/")} className={itemClass}>
                <SwbIcon className="size-5" /> Overview
              </Command.Item>
              {sections.map(({ name, to, icon: Icon }) => (
                <Command.Item key={to} value={name} onSelect={() => jump(to)} className={itemClass}>
                  <Icon aria-hidden className="size-4" /> {name}
                </Command.Item>
              ))}
              {projects.data?.projects.map(({ name, slug }) => (
                <Command.Item key={slug} value={`Project ${slug} ${name}`} onSelect={() => jump(`/projects/${encodeURIComponent(slug)}`)} className={itemClass}>
                  <FolderKanban aria-hidden className="size-4" /> {name}
                </Command.Item>
              ))}
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
