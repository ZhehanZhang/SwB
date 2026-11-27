import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { ArrowRight } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { AgentExchange } from '@/components/agent-exchange';
import { GitHubMark } from '@/components/github-mark';
import { HeroTitle } from '@/components/hero-title';
import { CopyInstall } from '@/components/landing-controls';
import { MarkTile } from '@/components/mark-tile';
import { SetupPicker } from '@/components/setup-picker';
import { SiteContainer } from '@/components/site-container';
import { SwitchboardHero } from '@/components/switchboard-hero';
import { deployProviders } from '@/lib/flags';
import { isLocale, localize } from '@/lib/i18n';
import { landingCopy, usageMarks } from '@/lib/landing-copy';
import { pageMetadata } from '@/lib/metadata';
import { promptFiles, readPrompt } from '@/lib/prompts';
import { tagline } from '@/lib/tagline';
import { releaseBranch, repository } from '@/lib/urls';

const sectionTitle = 'font-display text-3xl font-extrabold tracking-tight sm:text-4xl';
const deployBlock = 'grid min-w-0 grid-rows-[auto_1fr] gap-2';
const deployLink = 'swb-button grid flex-1 grid-cols-[3.25rem_minmax(0,1fr)_auto] border px-3 py-2 text-left hover:bg-fd-muted';
const compose = `git clone -b ${releaseBranch} ${repository}.git
cd SwB
mkdir -p data
SWB_UID="$(id -u)" docker compose up`;

export async function generateMetadata({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  return pageMetadata(lang, '/', 'SwB', tagline[lang]);
}

function prompts() {
  return Object.fromEntries(promptFiles().map((file) => {
    const name = file.slice(0, -3);
    return [name, readPrompt(name)];
  }));
}

export default async function Landing({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const copy = landingCopy[lang];
  const license = `${repository}/blob/${releaseBranch}/LICENSE`;
  const selfHosting = localize(lang, '/docs/self-hosting');
  const { render } = deployProviders;

  return (
    <>
      <HomeLayout nav={{ enabled: false }}>
        <SiteContainer>
          <section className="swb-hero-grid" aria-labelledby="landing-heading">
            <div className="swb-hero-copy">
              <HeroTitle stem={copy.hero.stem} endings={copy.hero.endings} between={copy.hero.between}
                names={{ claude: 'Claude', 'grok-bot': 'Grok Bot', capy: 'Capy Captain', dots: 'ChatGPT dots' }} />
              <div className="mt-6 grid w-fit max-w-full justify-items-start gap-2.5">
                <div className="flex w-full items-center gap-2.5 [&>.swb-button]:h-11 [&>.swb-button]:flex-1" data-hero-actions>
                  <SetupPicker lang={lang} modal />
                  <a className="swb-button border hover:bg-fd-muted" href={repository}>
                    <GitHubMark />GitHub
                  </a>
                </div>
                <CopyInstall lang={lang} />
              </div>
              <p className="mt-3 text-xs text-fd-muted-foreground">
                <a className="underline underline-offset-4 hover:text-fd-foreground" href={license}>{copy.openSource}</a>
              </p>
            </div>
            <SwitchboardHero label={copy.boardAlt} hint={copy.boardHint} lang={lang} prompts={prompts()} labels={copy.panel} />
          </section>
          <section className="py-12 sm:py-16" data-landing-section aria-labelledby="landing-demos">
            <h2 id="landing-demos" className={sectionTitle}>{copy.positioning.title}</h2>
            <p className="mt-3 text-base text-fd-muted-foreground sm:text-lg">{copy.positioning.lead}</p>
            <AgentExchange lang={lang} />
          </section>
          <section className="border-t py-12 sm:py-16" data-landing-section aria-labelledby="landing-deploy">
            <h2 id="landing-deploy" className={sectionTitle}>{copy.deployment.title}</h2>
            <div className="mt-7 grid auto-rows-fr gap-6 min-[900px]:grid-cols-3" data-deploy-grid>
              {[[copy.deployment.local, copy.install], [copy.deployment.server, compose]].map(([title, command]) => (
                <div className={deployBlock} key={title}>
                  <h3 className="text-base font-semibold">{title}</h3>
                  <pre className="wrap-anywhere whitespace-pre-wrap rounded-lg border bg-fd-muted p-3 text-sm leading-6"><code>{command}</code></pre>
                </div>
              ))}
              <div className={deployBlock}>
                <h3 className="text-base font-semibold">{copy.deployment.cloud}</h3>
                <div className="flex flex-col gap-2">
                  <Link className={deployLink} href={`${selfHosting}#fly`}>
                    <strong>Fly.io</strong>{' '}
                    <span className="text-fd-muted-foreground">{copy.deployment.flyClaim}</span>
                    <ArrowRight aria-hidden="true" className="size-4 shrink-0" />
                  </Link>
                  {render.enabled && render.href && (
                    <a className={deployLink} href={render.href}>
                      <strong>{render.name}</strong>{' '}
                      <span className="text-fd-muted-foreground">{copy.deployment.renderClaim}</span>
                      <ArrowRight aria-hidden="true" className="size-4 shrink-0" />
                    </a>
                  )}
                </div>
              </div>
            </div>
            <Link className="swb-text-link mt-5 inline-flex text-sm" href={selfHosting}>{copy.deployment.selfHosting}</Link>
          </section>
          <section className="border-t py-12 sm:py-16" data-landing-section aria-labelledby="landing-usage">
            <h2 id="landing-usage" className={sectionTitle}>{copy.usage.title}</h2>
            <div className="mt-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {copy.usage.examples.map((text, index) => (
                <div className="grid content-start gap-3 rounded-lg border bg-fd-card p-4" key={text}>
                  <div className="flex gap-1.5">
                    {usageMarks[index].map((mark) => <MarkTile key={mark} kind={mark} className="swb-win-avatar" />)}
                  </div>
                  <p className="text-sm leading-6">{text}</p>
                </div>
              ))}
            </div>
          </section>
        </SiteContainer>
      </HomeLayout>
      <footer className="border-t">
        <SiteContainer className="flex flex-wrap items-center gap-x-5 gap-y-3 py-5 text-sm text-fd-muted-foreground">
          <Link className="swb-text-link" href={localize(lang, '/docs')}>{copy.docs}</Link>
          <a className="swb-text-link" href={repository}>GitHub</a>
          <Link className="swb-text-link" href={localize(lang, '/about')}>{copy.about}</Link>
          <a className="swb-text-link" href={license}>{copy.license}</a>
          <Link className="swb-text-link" href={localize(lang, '/docs/trademarks')}>{copy.trademarks}</Link>
          <a className="swb-text-link" href="mailto:hello@swb.one">hello@swb.one</a>
        </SiteContainer>
      </footer>
    </>
  );
}
