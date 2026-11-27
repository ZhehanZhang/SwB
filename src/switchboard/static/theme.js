(() => {
  const key = "swb-theme"
  const root = document.documentElement
  const system = window.matchMedia("(prefers-color-scheme: dark)")
  const colorScheme = document.querySelector('meta[name="color-scheme"]')
  const colors = ["light", "dark"].map(scheme => {
    const meta = document.querySelector(`meta[name="theme-color"][media="(prefers-color-scheme: ${scheme})"]`)
    return { meta, color: meta?.content }
  })
  const read = () => {
    try { return localStorage.getItem(key) } catch { return null }
  }
  const mode = value => value === "light" || value === "dark" ? value : "system"
  let preference = mode(read())
  const apply = () => {
    const dark = preference === "dark" || (preference === "system" && system.matches)
    const scheme = dark ? "dark" : "light"
    const background = colors[dark ? 1 : 0].color
    root.classList.toggle("dark", dark)
    root.dataset.theme = scheme
    root.dataset.themeMode = preference
    root.style.colorScheme = scheme
    if (background) root.style.backgroundColor = background
    if (colorScheme) colorScheme.content = preference === "system" ? "light dark" : scheme
    for (const { meta, color } of colors) {
      if (meta) meta.content = preference === "system" ? color : background ?? color
    }
  }
  const update = value => {
    preference = mode(value)
    apply()
    window.dispatchEvent(new Event("swb-theme-change"))
  }
  window.swbTheme = {
    get: () => preference,
    set: value => {
      update(value)
      try { localStorage.setItem(key, preference) } catch {}
    },
  }
  apply()
  system.addEventListener("change", apply)
  window.addEventListener("storage", event => {
    if (event.key === key || event.key === null) update(read())
  })
})()
