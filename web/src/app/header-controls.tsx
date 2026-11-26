import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Command } from "cmdk"
import { ExternalLink, LayoutList, LogOut, Monitor, Moon, Search, Sun, UserRound } from "lucide-react"
import { Dialog, Popover } from "radix-ui"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { sections } from "@/app/navigation"
import { Lamp } from "@/components/console/lamp"
import { DataState } from "@/components/data-state"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { setTheme, useThemeMode } from "@/lib/appearance"
import { healthQuery, markSignedOut, requestJson, sessionQuery, SignedOutError } from "@/lib/api"

const actionClass = "size-11 text-console-foreground hover:bg-console-line focus-visible:outline-lamp md:size-9"

export function HealthControl() {
  const health = useQuery(healthQuery)
  const report = health.isSuccess ? health.data : undefined
  const revision = report?.commit?.slice(0, 7)
  const state = health.isPending ? "Checking health"
    : health.isError ? "Health unavailable"
    : report?.status === "ok" ? "All lines up" : "Needs attention"
  const lamp = health.isError || health.isPending ? "off" : report?.status === "ok" ? "on" : "waiting"

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={`${actionClass} xl:w-auto xl:px-2.5`}
          aria-label={`Health: ${state}${revision ? `, revision ${revision}` : ""}`}
          title={state}
        >
          <Lamp state={lamp} />
          <span className="hidden items-center gap-2 xl:inline-flex" aria-hidden>
            {state}
            {revision && <span className="font-mono text-xs text-console-muted">{revision}</span>}
          </span>
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          aria-label="Switchboard health"
          className="z-50 w-72 max-w-[calc(100vw-1rem)] rounded-md border bg-popover p-3 text-sm text-popover-foreground"
          align="end"
          sideOffset={8}
          collisionPadding={8}
        >
          {health.isPending ? <DataState kind="loading" message="Checking health…" className="py-1" />
            : health.isError ? (
              <DataState kind="error" message="Health unavailable." onRetry={() => void health.refetch()} className="py-1" />
            ) : (
              <>
                <div role="status" className="flex flex-wrap items-center gap-x-2.5 gap-y-1 font-medium">
                  <Lamp state={lamp} /> {state}
                  {revision && <span className="font-mono text-xs text-muted-foreground">{revision}</span>}
                </div>
                {!!report?.problems.length && (
                  <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-auto pl-5 break-words text-muted-foreground">
                    {report.problems.map((problem, index) => <li key={index}>{problem}</li>)}
                  </ul>
                )}
              </>
            )}
          <a className="mt-3 inline-flex items-center gap-1.5 rounded-sm underline underline-offset-2" href="https://status.example.com">
            Service status <ExternalLink aria-hidden className="size-3.5" />
          </a>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

export function CommandMenu() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.repeat || event.altKey || event.shiftKey) return
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return
      event.preventDefault()
      setOpen((value) => !value)
    }
    window.addEventListener("keydown", onKeyDown)
    return () => window.removeEventListener("keydown", onKeyDown)
  }, [])

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={`${actionClass} lg:w-auto lg:px-2`}
          aria-label="Open command menu"
          aria-keyshortcuts="Meta+K Control+K"
          title="Open command menu"
        >
          <Search aria-hidden />
          <kbd aria-hidden className="hidden font-mono text-xs text-console-muted lg:inline">⌘K</kbd>
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-x-4 top-[min(16vh,8rem)] z-50 mx-auto max-w-lg overflow-hidden rounded-md border bg-card text-card-foreground"
        >
          <Dialog.Title className="sr-only">Command menu</Dialog.Title>
          <Command label="Find a page">
            <div className="flex items-center gap-2 border-b px-4">
              <Search aria-hidden className="size-4 text-muted-foreground" />
              <Command.Input placeholder="Find a page…" autoFocus className="min-h-12 w-full bg-transparent text-sm outline-none" />
            </div>
            <Command.List className="max-h-[min(20rem,50dvh)] overflow-y-auto p-1.5">
              <Command.Empty className="px-2">
                <DataState kind="empty" message="No pages found." />
              </Command.Empty>
              {sections.map(({ name, to, icon: Icon }) => (
                <Command.Item
                  key={to}
                  value={name}
                  onSelect={() => {
                    setOpen(false)
                    void navigate(to)
                  }}
                  className="flex min-h-10 cursor-pointer items-center gap-2 rounded-sm px-2.5 text-sm select-none data-[selected=true]:bg-muted"
                >
                  <Icon aria-hidden className="size-4" /> {name}
                </Command.Item>
              ))}
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

const themes = [
  { mode: "system", name: "System", icon: Monitor },
  { mode: "light", name: "Light", icon: Sun },
  { mode: "dark", name: "Dark", icon: Moon },
] as const

export function AccountMenu() {
  const { data: session } = useQuery(sessionQuery)
  const theme = useThemeMode()
  const queryClient = useQueryClient()
  const logout = useMutation({
    mutationFn: () => requestJson("/auth/logout", (value): value is undefined => value === undefined, {
      method: "POST",
      csrf: session?.csrf,
    }),
    onSuccess: () => markSignedOut(queryClient),
    onError: (error) => {
      if (!(error instanceof SignedOutError)) toast.error("Couldn’t sign out.")
    },
  })

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className={actionClass} aria-label="Account" title="Account">
          <span className="grid size-7 place-items-center rounded-full border border-console-line" aria-hidden>
            <UserRound />
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" collisionPadding={8} className="w-60">
        <DropdownMenuLabel className="px-2.5 py-2 font-medium">Account</DropdownMenuLabel>
        <DropdownMenuSeparator className="my-1 border-t" />
        <DropdownMenuRadioGroup aria-label="Theme" value={theme} onValueChange={setTheme}>
          {themes.map(({ mode, name, icon: Icon }) => (
            <DropdownMenuRadioItem key={mode} value={mode}>
              <Icon aria-hidden /> {name}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator className="my-1 border-t" />
        <DropdownMenuItem asChild>
          <a href="/classic"><LayoutList aria-hidden /> Classic console</a>
        </DropdownMenuItem>
        {session?.via === "access" ? (
          <DropdownMenuItem asChild>
            <a href="/cdn-cgi/access/logout"><LogOut aria-hidden /> Sign out</a>
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem disabled={!session?.csrf || logout.isPending} onSelect={() => logout.mutate()}>
            <LogOut aria-hidden /> Sign out
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
