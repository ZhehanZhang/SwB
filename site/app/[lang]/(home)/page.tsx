import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { ThemeSwitch } from 'fumadocs-ui/layouts/shared/slots/theme-switch';
import { ArrowRight, BookOpen, ChevronDown, Package, Plug } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';
import { AgentExchange } from '@/components/agent-exchange';
import { BlockHeader, GuideLink } from '@/components/block-actions';
import { deployRowClass, DeployProviders, DeployTile } from '@/components/deploy-providers';
import { GitHubMark } from '@/components/github-mark';
import { HeroTitle } from '@/components/hero-title';
import { CopyInstall } from '@/components/landing-controls';
import { MarkTile } from '@/components/mark-tile';
import { SiteContainer } from '@/components/site-container';
import { SwitchboardHero } from '@/components/switchboard-hero';
import { isLocale, localize } from '@/lib/i18n';
import { landingCopy, usageMarks } from '@/lib/landing-copy';
import { pageMetadata } from '@/lib/metadata';
import { getStartedHref } from '@/lib/setup';
import { tagline } from '@/lib/tagline';
import { releaseBranch, repository } from '@/lib/urls';

const sectionTitle = 'font-display text-3xl font-extrabold tracking-tight sm:text-4xl';
const deployIntro = 'text-sm leading-5 text-fd-muted-foreground';
const compose = `git clone -b ${releaseBranch} \\
  ${repository}.git
cd SwB
mkdir -p data
SWB_UID="$(id -u)" docker compose up`;

export async function generateMetadata({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  return pageMetadata(lang, '/', 'SwB', tagline[lang]);
}

function DeployOption({ heading, intro, children, guide, secondary }: {
  heading: string; intro: string; children: ReactNode; guide?: ReactNode; secondary: ReactNode;
}) {
  return (
    <div className="grid min-w-0 gap-2 min-[900px]:row-span-4 min-[900px]:grid-rows-subgrid" data-deploy-option>
      <BlockHeader guides={guide}><h3 className="text-base font-semibold">{heading}</h3></BlockHeader>
      <p className={deployIntro}>{intro}</p>
      <div className="grid min-w-0" data-deploy-primary>{children}</div>
      <div className="grid min-w-0" data-deploy-secondary>{secondary}</div>
    </div>
  );
}

function DeployCommand({ children }: { children: string }) {
  return <pre className="overflow-x-auto whitespace-pre rounded-lg border bg-fd-muted px-3 py-2 font-mono text-[13px] leading-[18px]"><code>{children}</code></pre>;
}

export default async function Landing({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const copy = landingCopy[lang];
  const deploy = copy.deployment;
  const license = `${repository}/blob/${releaseBranch}/LICENSE`;
  const selfHosting = localize(lang, '/docs/self-hosting');

  return (
    <>
      <HomeLayout nav={{ enabled: false }}>
        <SiteContainer>
          <section className="swb-hero-grid" aria-labelledby="landing-heading">
            <div className="swb-hero-copy">
              <HeroTitle {...copy.hero}
                names={{ claude: 'Claude', 'grok-bot': 'Grok Bot', capy: 'Capy Captain', dots: 'ChatGPT dots' }} />
              <div className="mt-6 grid justify-items-start gap-4 lg:flex lg:flex-wrap lg:items-center lg:gap-x-4 lg:gap-y-2">
                <div className="flex items-center gap-2" data-hero-actions>
                  <Link className="swb-button swb-button-primary whitespace-nowrap" href={getStartedHref(lang)} prefetch={false}>
                    <Plug aria-hidden="true" />{copy.setupWithAgent}
                  </Link>
                  <a className="swb-button swb-button-outline" href={repository}>
                    <GitHubMark />GitHub
                  </a>
                </div>
                <p className="text-xs text-fd-muted-foreground lg:order-last lg:basis-full lg:pt-1">
                  <a className="hover:text-fd-foreground hover:underline hover:underline-offset-4" href={license}>{copy.openSource}</a>
                </p>
                <CopyInstall lang={lang} />
              </div>
            </div>
            <SwitchboardHero label={copy.boardAlt} hint={copy.boardHint} lang={lang} />
          </section>
          <section className="py-12 sm:py-16" data-landing-section aria-labelledby="landing-demos">
            <h2 id="landing-demos" className={sectionTitle}>{copy.positioning.title}</h2>
            <AgentExchange lang={lang} />
          </section>
          <section className="border-t py-12 sm:py-16" data-landing-section aria-labelledby="landing-deploy">
            <h2 id="landing-deploy" className={sectionTitle}>{deploy.title}</h2>
            <div className="mt-6 grid gap-6 min-[900px]:grid-cols-[minmax(0,1fr)_minmax(0,1.12fr)_minmax(0,1fr)] min-[900px]:gap-x-4 min-[900px]:gap-y-2 lg:gap-x-6" data-deploy-grid>
              <DeployOption heading={deploy.local} intro={deploy.localIntro}
                guide={<GuideLink href={localize(lang, '/docs/install')}>{deploy.localGuide}</GuideLink>}
                secondary={
                  <details className="group/pipx relative">
                    <summary className={`${deployRowClass} cursor-pointer list-none [&::-webkit-details-marker]:hidden`}>
                      <Package aria-hidden="true" />
                      <strong className="font-semibold">{deploy.pipxAction}</strong>
                      <ChevronDown aria-hidden="true" className="text-fd-muted-foreground group-open/pipx:rotate-180" />
                    </summary>
                    <div className="mt-2 min-[900px]:absolute min-[900px]:inset-x-0 min-[900px]:top-full min-[900px]:z-10">
                      <DeployCommand>{'pipx install swb-one\nswb up'}</DeployCommand>
                    </div>
                  </details>
                }>
                <DeployCommand>{copy.install}</DeployCommand>
              </DeployOption>
              <DeployOption heading={deploy.server} intro={deploy.serverIntro}
                guide={<GuideLink href={`${selfHosting}#docker-compose`}>{deploy.serverGuide}</GuideLink>}
                secondary={
                  <DeployTile href={`${selfHosting}#cloudflare-tunnel`} mark="cloudflare"
                    name="Cloudflare Tunnel" plan={deploy.freePlan} tunnel />
                }>
                <DeployCommand>{compose}</DeployCommand>
              </DeployOption>
              <DeployOption heading={deploy.cloud} intro={deploy.cloudIntro}
                secondary={
                  <Link href={selfHosting} className={deployRowClass}>
                    <BookOpen aria-hidden="true" />
                    <strong className="font-semibold">{deploy.cloudGuide}</strong>
                    <ArrowRight aria-hidden="true" className="text-fd-muted-foreground" />
                  </Link>
                }>
                <DeployProviders lang={lang} landing />
              </DeployOption>
            </div>
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
      <footer data-site-footer className="border-t">
        <SiteContainer className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-5">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3 text-sm text-fd-muted-foreground">
            <Link className="swb-text-link" href={localize(lang, '/docs')}>{copy.docs}</Link>
            <a className="swb-text-link" href={repository}>GitHub</a>
            <Link className="swb-text-link" href={localize(lang, '/about')}>{copy.about}</Link>
            <a className="swb-text-link" href={license}>{copy.license}</a>
            <a className="swb-text-link" href="mailto:hello@swb.one">hello@swb.one</a>
          </div>
          <ThemeSwitch className="swb-theme-toggle" />
        </SiteContainer>
      </footer>
    </>
  );
}
