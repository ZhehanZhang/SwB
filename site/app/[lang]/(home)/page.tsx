import Link from 'next/link';
import { notFound } from 'next/navigation';
import { ArrowRight } from 'lucide-react';
import { Brand } from '@/components/brand';
import { CopyInstall, LandingLanguage, LandingTheme } from '@/components/landing-controls';
import { isLocale, localize } from '@/lib/i18n';
import { landingCopy } from '@/lib/landing-copy';
import { pageMetadata } from '@/lib/metadata';
import { repository } from '@/lib/source';

const connectorIcons = [
  '/icons/claude.svg',
  '/avatars/chatgpt-96.webp',
  '/icons/gemini.svg',
  '/avatars/claude-code-96.webp',
  '/avatars/codex-96.webp',
  '/avatars/capy-96.webp',
  '/icons/grok-routine.svg',
  '/icons/slack.svg',
  '/icons/discord.svg',
  '/icons/telegram.svg',
  '/icons/github.svg',
];

export async function generateMetadata({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const { brand, intro } = landingCopy[lang];
  return pageMetadata(lang, '/', brand, intro);
}

export default async function Landing({ params }: PageProps<'/[lang]'>) {
  const { lang } = await params;
  if (!isLocale(lang)) notFound();
  const copy = landingCopy[lang];
  const docs = localize(lang, '/docs');
  const home = localize(lang, '/');

  return (
    <div className="min-h-dvh">
      <header>
        <nav className="mx-auto flex min-h-16 max-w-6xl flex-wrap items-center justify-between gap-x-3 px-5 py-2.5 sm:px-8">
          <Link href={home}><Brand name={copy.brand} /></Link>
          <div className="flex items-center gap-2.5 text-sm sm:gap-5">
            <Link className="swb-text-link" href={docs}>{copy.docs}</Link>
            <a className="swb-text-link" href={repository}>{copy.github}</a>
            <LandingLanguage lang={lang} />
            <LandingTheme lang={lang} />
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-5 sm:px-8">
        <section className="flex flex-col items-center py-14 text-center sm:py-20 lg:py-24">
          <h1 className="text-display-xl swb-hero-title">{copy.headline}</h1>
          <p className="mt-3 text-fd-muted-foreground sm:text-lg">{copy.intro}</p>
          <div className="mt-7"><CopyInstall lang={lang} /></div>
          <div className="mt-5 flex flex-wrap justify-center gap-2.5">
            <Link className="swb-button bg-fd-primary text-fd-primary-foreground hover:bg-fd-primary/85" href={docs}>
              {copy.docs}<ArrowRight aria-hidden="true" className="size-4" />
            </Link>
            <a className="swb-button border hover:bg-fd-muted" href={repository}>{copy.github}</a>
          </div>
        </section>
        <figure className="overflow-hidden rounded-lg">
          <img className="block h-auto w-full dark:hidden" width={2400} height={1350}
            src="/shots/hero-light.webp" srcSet="/shots/hero-light-1200.webp 1200w, /shots/hero-light.webp 2400w"
            sizes="(min-width: 1152px) 1088px, 100vw" alt={copy.shotAlt} fetchPriority="high" />
          <img className="hidden h-auto w-full dark:block" width={2400} height={1350}
            src="/shots/hero-dark.webp" srcSet="/shots/hero-dark-1200.webp 1200w, /shots/hero-dark.webp 2400w"
            sizes="(min-width: 1152px) 1088px, 100vw" alt={copy.shotAlt} loading="lazy" />
        </figure>
        <div className="grid grid-cols-3 gap-y-6 py-10 min-[500px]:grid-cols-6 lg:grid-cols-11 lg:py-14">
          {copy.connectors.map((name, index) => (
            <figure key={name} className="flex flex-col items-center gap-2 text-center">
              <img className="swb-connector block size-8 rounded-sm object-contain" width={32} height={32}
                src={connectorIcons[index]} alt="" />
              <figcaption className="text-xs text-fd-muted-foreground">{name}</figcaption>
            </figure>
          ))}
        </div>
        <section className="grid gap-8 border-t py-10 md:grid-cols-3 md:gap-12 md:py-14">
          {copy.steps.map(({ title, description }, index) => (
            <div key={title}>
              <span className="text-display-xl block text-fd-muted-foreground" aria-hidden="true">{index + 1}</span>
              <h2 className="mt-2.5 font-display text-xl font-extrabold">{title}</h2>
              <p className="mt-1 text-sm text-fd-muted-foreground">{description}</p>
            </div>
          ))}
        </section>
      </main>
      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4 text-sm text-fd-muted-foreground sm:px-8">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <Link className="swb-text-link" href={docs}>{copy.docs}</Link>
            <a className="swb-text-link" href={repository}>{copy.github}</a>
            <Link className="swb-text-link" href={localize(lang, '/about')}>{copy.about}</Link>
            <a className="swb-text-link" href="https://www.apache.org/licenses/LICENSE-2.0">{copy.license}</a>
          </div>
          <LandingLanguage lang={lang} />
        </div>
      </footer>
    </div>
  );
}
