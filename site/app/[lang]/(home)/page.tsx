import Link from 'next/link';
import { notFound } from 'next/navigation';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { AgentExchange } from '@/components/agent-exchange';
import { GitHubMark } from '@/components/github-mark';
import { HeroTitle } from '@/components/hero-title';
import { CopyInstall } from '@/components/landing-controls';
import { SetupPicker } from '@/components/setup-picker';
import { SwitchboardHero } from '@/components/switchboard-hero';
import { exchangeCopy } from '@/lib/exchange-copy';
import { isLocale, localize } from '@/lib/i18n';
import { landingCopy } from '@/lib/landing-copy';
import { baseOptions } from '@/lib/layout.shared';
import { pageMetadata } from '@/lib/metadata';
import { promptFiles, readPrompt } from '@/lib/prompts';
import { repository } from '@/lib/source';
import { tagline } from '@/lib/tagline';

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

  return (
    <HomeLayout {...baseOptions(lang)}>
      <main className="mx-auto w-full max-w-7xl px-5 sm:px-8">
        <section className="swb-hero-grid">
          <div className="swb-hero-copy">
            <HeroTitle stem={copy.hero.stem} endings={copy.hero.endings} between={copy.hero.between}
              names={{ claude: 'Claude', 'grok-bot': 'Grok Bot', capy: 'Captain', dots: 'ChatGPT dots' }} />
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <SetupPicker lang={lang} modal />
              <a className="swb-button border hover:bg-fd-muted" href={repository}>
                <GitHubMark />GitHub
              </a>
            </div>
            <div className="mt-3"><CopyInstall lang={lang} /></div>
          </div>
          <SwitchboardHero label={copy.boardAlt} lang={lang} prompts={prompts()} labels={copy.panel} />
        </section>
        <section className="pb-16 pt-6 sm:pb-24 sm:pt-10">
          <h2 className="font-display text-3xl font-extrabold tracking-tight sm:text-4xl">{copy.positioning.title}</h2>
          <div className="mt-5 grid max-w-5xl gap-4 text-[17px] leading-relaxed text-fd-muted-foreground md:grid-cols-3 md:gap-10">
            {copy.positioning.body.map((text) => <p key={text}>{text}</p>)}
          </div>
          <h3 className="sr-only">{exchangeCopy[lang].title}</h3>
          <AgentExchange lang={lang} />
        </section>
      </main>
      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-3 px-5 py-5 text-sm text-fd-muted-foreground sm:px-8">
          <Link className="swb-text-link" href={localize(lang, '/docs')}>{copy.docs}</Link>
          <a className="swb-text-link" href={repository}>GitHub</a>
          <Link className="swb-text-link" href={localize(lang, '/about')}>{copy.about}</Link>
          <a className="swb-text-link" href="https://www.apache.org/licenses/LICENSE-2.0">{copy.license}</a>
        </div>
      </footer>
    </HomeLayout>
  );
}
