import Link from 'next/link';
import { deployProviders } from '@/lib/flags';
import { localize, type Locale } from '@/lib/i18n';

export function ProviderMark({ name }: { name: 'fly' | 'render' | 'railway' | 'cloudflare' }) {
  const size = name === 'render' ? 32 : 16;
  const offset = (16 - size) / 2;
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="inline-block size-4 shrink-0 align-[-.12em]">
      <image className="dark:hidden" href={`/marks/${name}-on-light.svg`} x={offset} y={offset} width={size} height={size} />
      <image className="hidden dark:inline" href={`/marks/${name}-on-dark.svg`} x={offset} y={offset} width={size} height={size} />
    </svg>
  );
}

export function DeployProviders({ lang }: { lang: Locale }) {
  return (
    <div className="not-prose grid max-w-sm gap-2" data-deploy-providers>
      {Object.values(deployProviders).map((provider) => {
        const { mark, name, label, enabled } = provider;
        const oneClickHref = 'oneClickHref' in provider ? provider.oneClickHref : undefined;
        if (!enabled && !oneClickHref) return null;
        const href = oneClickHref ?? provider.href;
        return (
          <Link
            className="swb-button swb-button-outline justify-start whitespace-nowrap px-3 py-2 text-left"
            href={href.startsWith('/') ? localize(lang, href) : href}
            key={name}
          >
            <ProviderMark name={mark} />
            <span className="flex items-baseline gap-2">
              <strong>{name}</strong>{' '}<span className="font-normal text-fd-muted-foreground">{label[lang]}</span>
            </span>
          </Link>
        );
      })}
    </div>
  );
}
