import { useQuery } from "@tanstack/react-query"
import { ChevronRight, ExternalLink } from "lucide-react"
import { Link } from "react-router-dom"
import { PRODUCT_NAME } from "@/app/brand"
import { CopyButton } from "@/components/copy-button"
import { DataState } from "@/components/data-state"
import { SwbIcon } from "@/components/swb-icon"
import { isObject, requestJson } from "@/lib/api"

const repository = "https://github.com/ZhehanZhang/SwB"
const openSourceLinks = [
  ["License", "Apache-2.0", repository],
  ["Repository", "github.com/ZhehanZhang/SwB", repository],
  ["Contributing", "GitHub repository", repository],
  ["Website", "swb.one", "https://swb.one"],
] as const
const projectLinks = [
  ["Docs", "README.md", `${repository}/blob/main/README.md`],
  ["Security policy", "GitHub repository", repository],
] as const

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

const row = "grid min-h-[39px] grid-cols-[35%_minmax(0,1fr)_auto] items-center border-b py-2 md:min-h-[41px] md:grid-cols-[33%_minmax(0,1fr)_auto]"
const label = "pe-3 wrap-anywhere text-muted-foreground"
const value = "wrap-anywhere font-mono text-mono-sm"
const link = `${row} group`

function ExternalLinks({ links }: { links: readonly (readonly [string, string, string])[] }) {
  return links.map(([title, detail, href]) => (
    <li key={title}>
      <a className={link} href={href} target="_blank" rel="noreferrer" aria-label={`${title}: ${detail}`}>
        <span className={label}>{title}</span>
        <span className={`${value} pe-2 group-hover:underline`}>{detail}</span>
        <ExternalLink aria-hidden className="size-3.5" />
      </a>
    </li>
  ))
}

function buildDate(value: string) {
  if (!value.includes("T")) return value
  const timestamp = Date.parse(value)
  return Number.isNaN(timestamp) ? value : `${new Date(timestamp).toISOString().slice(0, 16).replace("T", " ")} UTC`
}

export function AboutPage() {
  const about = useQuery({
    queryKey: ["about"],
    queryFn: ({ signal }) => requestJson("/api/about", isAbout, { signal }),
    staleTime: Infinity,
  })
  const commit = about.data?.commit
  const hasCommit = !!commit && /^[a-f0-9]{7,64}$/.test(commit)

  return (
    <div className="w-full max-w-[1104px] space-y-6 text-body-sm md:space-y-8 md:pt-2">
      <header className="flex items-center gap-3 md:gap-4">
        <SwbIcon className="size-10 md:size-12" />
        <div>
          <p className="font-display text-display-lg">{PRODUCT_NAME}</p>
          <p className="text-muted-foreground">A switchboard for the agents you already use.</p>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-2 lg:gap-x-16">
        <div className="min-w-0 space-y-6 md:space-y-8">
          <section aria-labelledby="about-version">
            <h2 id="about-version" className="text-heading-md">Version</h2>
            {about.isPending ? <DataState kind="loading" /> : about.isError ? (
              <DataState kind="error" message="Couldn’t load build information." onRetry={() => void about.refetch()} />
            ) : (
              <dl className="mt-2">
                <div className={row}>
                  <dt className={label}>Package</dt>
                  <dd className={value}>swb-one {about.data.version}</dd>
                </div>
                <div className={row}>
                  <dt className={label}>Commit</dt>
                  <dd className="col-span-2 flex min-w-0 items-center justify-between gap-2">
                    {hasCommit ? (
                      <>
                        <a className={`${value} hover:underline`} href={`${repository}/commit/${commit}`} target="_blank" rel="noreferrer" title={commit}>{commit.slice(0, 7)}</a>
                        <CopyButton text={commit} label="Copy commit" />
                      </>
                    ) : <span className={value}>{about.data.commit}</span>}
                  </dd>
                </div>
                <div className={row}>
                  <dt className={label}>Built</dt>
                  <dd className={value}>{buildDate(about.data.build_date)}</dd>
                </div>
              </dl>
            )}
          </section>

          {about.data && (
            <section aria-labelledby="about-blocks">
              <h2 id="about-blocks" className="text-heading-md">Blocks</h2>
              {about.data.blocks.length ? (
                <dl className="mt-2">
                  {about.data.blocks.map(block => (
                    <div key={block.name} className={row}>
                      <dt className={label}>{block.name}</dt>
                      <dd className={value}>{block.version}</dd>
                    </div>
                  ))}
                </dl>
              ) : <p className="mt-2 text-muted-foreground">No blocks enabled.</p>}
            </section>
          )}
        </div>

        <div className="min-w-0">
          <section aria-labelledby="about-open-source">
            <h2 id="about-open-source" className="text-heading-md">Open source</h2>
            <ul className="mt-2"><ExternalLinks links={openSourceLinks} /></ul>
          </section>

          <section aria-labelledby="about-links" className="mt-6 md:mt-8">
            <h2 id="about-links" className="text-heading-md">Links</h2>
            <ul className="mt-2">
              <ExternalLinks links={projectLinks} />
              <li>
                <Link className={link} to="/audit" aria-label="Audit trail">
                  <span className={label}>Audit trail</span>
                  <span className={`${value} pe-2 group-hover:underline`}>Audit</span>
                  <ChevronRight aria-hidden className="size-3.5" />
                </Link>
              </li>
            </ul>
          </section>

          <p className="mt-3 text-muted-foreground md:mt-8">
            SwB isn't affiliated with Slack, Discord, Telegram, Anthropic, OpenAI, xAI, GitHub or Capy. Logos only show what each block connects to.
          </p>
        </div>
      </div>
    </div>
  )
}
