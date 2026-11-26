import { useQuery } from "@tanstack/react-query"
import { ExternalLink } from "lucide-react"
import { Link } from "react-router-dom"
import { DataState } from "@/components/data-state"
import { isObject, requestJson } from "@/lib/api"

const repository = "https://github.com/ZhehanZhang/SwB"
const docs = `${repository}/blob/main/README.md`
const openSourceLinks = [
  ["Apache-2.0", repository],
  ["GitHub repository", repository],
  ["Contributing", repository],
  ["swb.one", "https://swb.one"],
] as const
const projectLinks = [["Documentation", docs], ["Security", repository]] as const

type About = {
  version: string
  commit: string
  build_date: string
  blocks: { name: string; id: string; version: string | null }[]
}

function isAbout(value: unknown): value is About {
  return isObject(value) && typeof value.version === "string" && typeof value.commit === "string"
    && typeof value.build_date === "string" && Array.isArray(value.blocks)
    && value.blocks.every((block: unknown) => isObject(block) && typeof block.name === "string"
      && typeof block.id === "string" && (block.version === null || typeof block.version === "string"))
}

const row = "grid min-h-[41px] grid-cols-[minmax(88px,24%)_minmax(0,1fr)] items-center gap-3 border-b py-2"
const link = "flex min-h-[41px] items-center justify-between gap-3 border-b py-2 hover:underline"

function ExternalLinks({ links }: { links: readonly (readonly [string, string])[] }) {
  return links.map(([label, href]) => (
    <li key={label}>
      <a className={link} href={href} target="_blank" rel="noreferrer">
        {label}<ExternalLink aria-hidden className="size-4 text-muted-foreground" />
      </a>
    </li>
  ))
}

export function AboutPage() {
  const about = useQuery({
    queryKey: ["about"],
    queryFn: ({ signal }) => requestJson("/api/about", isAbout, { signal }),
    staleTime: Infinity,
  })

  return (
    <div className="w-full max-w-[720px] space-y-8 pt-2 text-body-sm">
      <p className="max-w-prose leading-relaxed text-muted-foreground">
        SwB isn't affiliated with Slack, Discord, Telegram, Anthropic, OpenAI, xAI, GitHub or Capy. Logos only show what each block connects to.
      </p>

      <section aria-labelledby="about-open-source">
        <h2 id="about-open-source" className="text-heading-md">Open source</h2>
        <ul className="mt-2"><ExternalLinks links={openSourceLinks} /></ul>
      </section>

      <section aria-labelledby="about-version">
        <h2 id="about-version" className="text-heading-md">Version</h2>
        {about.isPending ? <DataState kind="loading" /> : about.isError ? (
          <DataState kind="error" message="Couldn’t load build information." onRetry={() => void about.refetch()} />
        ) : (
          <>
            <dl className="mt-2">
              <div className={row}><dt className="text-muted-foreground">Package</dt><dd className="wrap-anywhere font-mono text-mono-sm">{about.data.version}</dd></div>
              <div className={row}>
                <dt className="text-muted-foreground">Git commit</dt>
                <dd className="wrap-anywhere font-mono text-mono-sm">
                  {/^[a-f0-9]{7,64}$/.test(about.data.commit) ? (
                    <a href={`${repository}/commit/${about.data.commit}`} target="_blank" rel="noreferrer" className="hover:underline">{about.data.commit}</a>
                  ) : about.data.commit}
                </dd>
              </div>
              <div className={row}><dt className="text-muted-foreground">Build date</dt><dd className="wrap-anywhere font-mono text-mono-sm">{about.data.build_date}</dd></div>
            </dl>
            <h3 className="mt-4 text-label-sm">Enabled blocks</h3>
            {about.data.blocks.length ? (
              <ul>
                {about.data.blocks.map(block => (
                  <li key={block.name} className="flex min-h-[41px] items-center justify-between gap-3 border-b py-2">
                    <span className="wrap-anywhere">{block.name}</span>
                    {block.version && <code className="font-mono text-mono-sm">{block.version}</code>}
                  </li>
                ))}
              </ul>
            ) : <p className="mt-1 text-muted-foreground">No blocks enabled.</p>}
          </>
        )}
      </section>

      <section aria-labelledby="about-links">
        <h2 id="about-links" className="text-heading-md">Links</h2>
        <ul className="mt-2">
          <ExternalLinks links={projectLinks} />
          <li><Link className={link} to="/audit">Audit</Link></li>
        </ul>
      </section>
    </div>
  )
}
