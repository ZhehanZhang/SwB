import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Command } from "cmdk"
import { FolderKanban, Search } from "lucide-react"
import { Dialog } from "radix-ui"
import { useNavigate } from "react-router-dom"
import { PRODUCT_NAME } from "@/app/brand"
import { sections } from "@/app/navigation"
import { DataState } from "@/components/data-state"
import { SwbIcon } from "@/components/swb-icon"
import { requestJson } from "@/lib/api"

type Project = { slug: string; name: string }

function isProjectList(value: unknown): value is { projects: Project[] } {
  if (typeof value !== "object" || value === null || !("projects" in value) || !Array.isArray(value.projects)) return false
  return value.projects.every(p => p !== null && typeof p === "object" && typeof p.slug === "string" && typeof p.name === "string")
}

const itemClass = "flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 text-body-sm select-none data-[selected=true]:bg-muted"

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
        <button
          type="button"
          aria-label="Search or jump"
          aria-keyshortcuts="Meta+K Control+K"
          className="flex size-9 shrink-0 items-center justify-center gap-2 rounded-md text-body-sm text-muted-foreground hover:bg-muted md:w-[280px] md:justify-start md:border md:border-border-strong md:px-3"
        >
          <Search aria-hidden className="size-5 md:size-4" />
          <span className="hidden md:inline">Search or jump</span>
          <kbd aria-hidden className="ml-auto hidden font-mono text-mono-sm text-subtle-foreground md:inline">⌘K</kbd>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-foreground/25" />
        <Dialog.Content aria-describedby={undefined} className="fixed inset-x-5 top-[min(16dvh,8rem)] z-50 mx-auto max-w-lg overflow-hidden rounded-lg border bg-surface">
          <Dialog.Title className="sr-only">Search or jump</Dialog.Title>
          <Command label="Search or jump">
            <div className="flex items-center gap-2 border-b px-4">
              <Search aria-hidden className="size-4 text-muted-foreground" />
              <Command.Input placeholder="Search or jump" autoFocus className="min-h-12 w-full bg-transparent text-body-md outline-none focus-visible:outline-none" />
            </div>
            <Command.List className="max-h-[min(24rem,55dvh)] overflow-y-auto p-2">
              <Command.Empty className="px-2"><DataState kind="empty" message="No matches." /></Command.Empty>
              <Command.Item value={`${PRODUCT_NAME} Overview Home`} onSelect={() => jump("/")} className={itemClass}>
                <SwbIcon className="size-5" /> {PRODUCT_NAME}
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
