import { useSyncExternalStore } from "react"

export type Theme = "light" | "dark" | "system"

const cookieName = "siteAppearance"
const storageKey = "theme"
const appearanceChange = "switchboard:appearance-change"
const media = window.matchMedia("(prefers-color-scheme: dark)")
const root = document.documentElement

function isTheme(value: string | null | undefined): value is Theme {
  return value === "light" || value === "dark" || value === "system"
}

function readTheme(): Theme {
  const cookie = document.cookie.split(";").map((part) => part.trim())
    .find((part) => part.startsWith(`${cookieName}=`))
  const theme = cookie?.slice(cookieName.length + 1)
  if (isTheme(theme)) return theme

  try {
    const stored = localStorage.getItem(storageKey)
    if (isTheme(stored)) return stored
  } catch {
    return "system"
  }
  return "system"
}

function applyTheme() {
  const theme = readTheme()
  const dark = theme === "dark" || (theme === "system" && media.matches)
  root.classList.toggle("dark", dark)
  root.dataset.theme = dark ? "dark" : "light"
  root.dataset.themeMode = theme
  window.dispatchEvent(new Event(appearanceChange))
}

export function setTheme(value: string) {
  if (!isTheme(value)) return
  const host = location.hostname
  const domain = ["example.com", "example.com"].find((candidate) => host === candidate || host.endsWith(`.${candidate}`))
  document.cookie = `${cookieName}=${value}; Path=/; Max-Age=31536000; SameSite=Lax${domain ? `; Domain=${domain}` : ""}${location.protocol === "https:" ? "; Secure" : ""}`
  try {
    localStorage.setItem(storageKey, value)
  } catch {}
  applyTheme()
}

function subscribe(listener: () => void) {
  window.addEventListener(appearanceChange, listener)
  return () => window.removeEventListener(appearanceChange, listener)
}

export function useThemeMode() {
  return useSyncExternalStore(subscribe, () => root.dataset.themeMode ?? "system")
}

export function useResolvedTheme() {
  return useSyncExternalStore(subscribe, () => root.dataset.theme === "dark" ? "dark" : "light")
}

applyTheme()
media.addEventListener("change", applyTheme)
window.addEventListener("storage", (event) => {
  if (event.key === storageKey || event.key === null) applyTheme()
})
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") applyTheme()
})
