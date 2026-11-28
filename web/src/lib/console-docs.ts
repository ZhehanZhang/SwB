const deployAnchors = new Map([
  ["fly", "fly"],
  ["render", "render"],
  ["railway", "railway"],
  ["compose", "docker-compose"],
])
const backupAnchors = {
  en: "back-up-to-git",
  es: "copia-de-seguridad-en-git",
  fr: "sauvegarder-dans-git",
  de: "in-git-sichern",
  ko: "git에-백업",
  ja: "git-にバックアップする",
  "zh-Hans": "备份到-git",
  "zh-Hant": "備份到-git",
}

export function selectDocsLocale(consoleLocale: string | null | undefined, languages: readonly string[]) {
  for (const language of [consoleLocale, ...languages]) {
    if (!language) continue
    const [code, variant] = language.toLowerCase().split("-")
    if (code === "zh") return ["hant", "tw", "hk", "mo"].includes(variant) ? "zh-Hant" : "zh-Hans"
    if (Object.hasOwn(backupAnchors, code)) return code as keyof typeof backupAnchors
  }
  return "en"
}

export function consoleDocs({ platform, publicUrl, consoleLocale, languages = navigator.languages }: {
  platform?: string | null
  publicUrl?: string | null
  consoleLocale?: string | null
  languages?: readonly string[]
} = {}) {
  const locale = selectDocsLocale(consoleLocale, languages)
  const base = `https://swb.one${locale === "en" ? "" : `/${locale}`}/docs`
  const url = publicUrl && URL.canParse(publicUrl) ? new URL(publicUrl) : null
  const address = url && (url.protocol === "https:" || url.protocol === "http:")
    ? `swb=${encodeURIComponent(url.origin)}`
    : ""
  const link = (page: string, anchor?: string) => {
    const hash = [anchor && encodeURIComponent(anchor), address].filter(Boolean).join("&")
    return `${base}/${page}${hash ? `#${hash}` : ""}`
  }
  return {
    deploy: platform === "local" ? link("install", "install-and-start") : link("deploy", deployAnchors.get(platform ?? "")),
    operate: link("operate", "backups-and-upgrades"),
    tunnel: url?.hostname.endsWith(".trycloudflare.com") ? link("deploy", "cloudflare-tunnel") : null,
    memoryBackup: link("memory", backupAnchors[locale]),
  }
}
