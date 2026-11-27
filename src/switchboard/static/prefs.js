document.addEventListener("click", event => {
  if (!event.target.closest?.("[data-theme-cycle]")) return
  const themes = ["system", "dark", "light"]
  const theme = window.swbTheme
  theme.set(themes[(themes.indexOf(theme.get()) + 1) % themes.length])
})
