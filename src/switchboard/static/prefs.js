// The swbAppearance theme cookie, scoped to this host. A legacy siteAppearance cookie is read as a fallback.
(function () {
  var root = document.documentElement
  var media = matchMedia('(prefers-color-scheme: dark)')
  function read(name) {
    var hit = document.cookie.split('; ').filter(function (c) { return c.indexOf(name + '=') === 0 })[0]
    return hit ? decodeURIComponent(hit.slice(name.length + 1)) : ''
  }
  function get() {
    var v = read('swbAppearance') || read('siteAppearance')
    if (v === 'light' || v === 'dark' || v === 'system') return v
    try { v = localStorage.getItem('theme') } catch (e) { v = null }
    return v === 'light' || v === 'dark' || v === 'system' ? v : 'system'
  }
  function apply(theme) {
    theme = theme || get()
    var dark = theme === 'dark' || (theme === 'system' && media.matches)
    root.classList.toggle('dark', dark)
    root.dataset.theme = dark ? 'dark' : 'light'
    root.dataset.themeMode = theme
    root.style.colorScheme = dark ? 'dark' : 'light'
    root.style.backgroundColor = dark ? '#1c1917' : '#f5f5f4'
  }
  function set(theme) {
    document.cookie = 'swbAppearance=' + encodeURIComponent(theme) + '; Path=/; Max-Age=31536000; SameSite=Lax' + (location.protocol === 'https:' ? '; Secure' : '')
    try { localStorage.setItem('theme', theme) } catch (e) {}
    apply(theme)
  }
  apply()
  media.addEventListener('change', function () { apply() })
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') apply() })
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-theme-cycle]')
    if (!b) return
    var order = media.matches ? ['system', 'light', 'dark'] : ['system', 'dark', 'light']
    set(order[(order.indexOf(get()) + 1) % order.length])
  })
})()
