import { ArrowRight, ArrowUpRight } from 'lucide-react';
import Link from 'next/link';
import { deployProviders } from '@/lib/flags';
import { localize, type Locale } from '@/lib/i18n';

type Mark = 'fly' | 'render' | 'railway' | 'cloudflare';

export const deployRowClass = 'swb-button swb-button-outline grid min-h-11 grid-cols-[auto_minmax(0,1fr)_auto] items-center justify-start px-3 py-[3px] text-start leading-5 whitespace-nowrap transition-colors active:bg-fd-border motion-reduce:transition-none';

export function ProviderMark({ name }: { name: Mark }) {
  const size = name === 'render' ? 32 : 16;
  const offset = (16 - size) / 2;
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="inline-block size-4 shrink-0 align-[-.12em]">
      <image className="dark:hidden" href={`/marks/${name}-on-light.svg`} x={offset} y={offset} width={size} height={size} />
      <image className="hidden dark:inline" href={`/marks/${name}-on-dark.svg`} x={offset} y={offset} width={size} height={size} />
    </svg>
  );
}

export function DeployTile({ href, mark, name, plan, tunnel = false, grouped = false }: {
  href: string; mark: Mark; name: string; plan: string; tunnel?: boolean; grouped?: boolean;
}) {
  const Arrow = href.startsWith('/') ? ArrowRight : ArrowUpRight;
  return (
    <Link href={href} data-provider-tile={mark} data-deploy-tunnel={tunnel || undefined}
      className={grouped ? `${deployRowClass} rounded-none border-0 not-last:border-b focus-visible:-outline-offset-2 min-[900px]:min-h-0 min-[900px]:py-0` : deployRowClass}>
      {tunnel ? (
        <svg aria-hidden="true" viewBox="0 0 16 16"><image href="/marks/cloudflare.svg" width="16" height="16" /></svg>
      ) : <ProviderMark name={mark} />}
      <span className="grid min-w-0 grid-cols-[auto_auto] items-baseline justify-start gap-x-2" data-provider-text>
        <strong className="whitespace-nowrap font-semibold">{name}</strong>{' '}
        <span className="text-xs leading-4 font-normal whitespace-nowrap text-fd-muted-foreground" data-provider-plan>{plan}</span>
      </span>
      <Arrow aria-hidden="true" className="text-fd-muted-foreground" data-provider-arrow />
    </Link>
  );
}

export function DeployProviders({ lang, landing = false }: { lang: Locale; landing?: boolean }) {
  return (
    <div className={landing
      ? 'grid auto-rows-fr overflow-hidden rounded-md border border-(--border-strong) min-[900px]:[contain:size]'
      : 'not-prose grid gap-3'}
      data-deploy-providers>
      {Object.values(deployProviders).map((provider) => {
        const { mark, name, label, enabled } = provider;
        const oneClickHref = 'oneClickHref' in provider ? provider.oneClickHref : undefined;
        if (!enabled && !oneClickHref) return null;
        const href = landing && mark === 'render' ? provider.href : oneClickHref ?? provider.href;
        return (
          <DeployTile key={name} href={href.startsWith('/') ? localize(lang, href) : href}
            mark={mark} name={name} plan={label[lang]} grouped={landing} />
        );
      })}
    </div>
  );
}
