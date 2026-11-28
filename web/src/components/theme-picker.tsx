import { useSyncExternalStore } from "react"
import { RadioGroup } from "radix-ui"

const themes = ["light", "dark", "system"] as const
type Theme = typeof themes[number]

declare global {
  interface Window {
    swbTheme?: { get: () => Theme; set: (theme: Theme) => void }
  }
}

function subscribe(listener: () => void) {
  window.addEventListener("swb-theme-change", listener)
  return () => window.removeEventListener("swb-theme-change", listener)
}

export function ThemePicker() {
  const preference = useSyncExternalStore(subscribe, () => window.swbTheme?.get() ?? "system")
  return (
    <RadioGroup.Root aria-label="Theme" orientation="horizontal" value={preference} disabled={!window.swbTheme}
      onValueChange={value => {
        const theme = themes.find(theme => theme === value)
        if (theme) window.swbTheme?.set(theme)
      }}
      className="inline-flex gap-1 rounded-md bg-surface-sunken p-1"
    >
      {themes.map(theme => (
        <RadioGroup.Item key={theme} value={theme}
          className="rounded-sm px-3 py-1 text-label-sm text-muted-foreground capitalize data-[state=checked]:bg-surface data-[state=checked]:text-foreground data-[state=checked]:shadow-xs"
        >
          {theme}
        </RadioGroup.Item>
      ))}
    </RadioGroup.Root>
  )
}
