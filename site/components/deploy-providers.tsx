import { Plane } from 'lucide-react';
import Link from 'next/link';
import { deployProviders } from '@/lib/flags';
import { localize, type Locale } from '@/lib/i18n';

function ProviderMark({ name }: { name: 'plane' | 'render' | 'railway' }) {
  if (name === 'plane') return <Plane aria-hidden="true" />;

  const size = name === 'render' ? 32 : 16;
  const offset = (16 - size) / 2;
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16">
      <image className="dark:hidden" href={`/marks/${name}-on-light.svg`} x={offset} y={offset} width={size} height={size} />
      <image className="hidden dark:inline" href={`/marks/${name}-on-dark.svg`} x={offset} y={offset} width={size} height={size} />
    </svg>
  );
}

export function DeployProviders({ lang }: { lang: Locale }) {
  return (
    <div className="not-prose grid max-w-sm gap-2" data-deploy-providers>
      {Object.values(deployProviders).filter(({ enabled }) => enabled).map((provider) => {
        const { mark, name, label } = provider;
        const href = 'blueprint' in provider ? provider.blueprint ?? provider.href : provider.href;
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
