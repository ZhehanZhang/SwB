(() => {
  const key = "swb-theme"
  const system = window.matchMedia("(prefers-color-scheme: dark)")
  const read = () => {
    try { return localStorage.getItem(key) } catch { return null }
  }
  const mode = value => value === "light" || value === "dark" ? value : "system"
  let preference = mode(read())
  const apply = () => document.documentElement.classList.toggle("dark", preference === "dark" || (preference === "system" && system.matches))
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
