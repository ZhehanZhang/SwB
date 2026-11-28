import type { Metadata } from 'next';
import { locales, localize, type Locale } from './i18n';
import { siteUrl } from './urls';

export function languageAlternates(hrefFor: (lang: Locale) => string | undefined, defaultHref = hrefFor('en')) {
  const alternates: Record<string, string> = {};
  for (const lang of locales) {
    const href = hrefFor(lang);
    if (href) alternates[lang] = new URL(href, siteUrl).href;
  }
  if (defaultHref) alternates['x-default'] = new URL(defaultHref, siteUrl).href;
  return alternates;
}

export function pageMetadata(
  lang: Locale,
  path: string,
  title: string,
  description?: string,
  page?: { url: string; hrefFor: (lang: Locale) => string | undefined; defaultHref?: string; markdown?: boolean },
): Metadata {
  const url = page?.url ?? localize(lang, path);
  const image = '/og/swb-board.png';

  return {
    title,
    description,
    alternates: {
      canonical: url,
      languages: languageAlternates(page?.hrefFor ?? ((lang) => localize(lang, path)), page?.defaultHref),
      types: page && page.markdown !== false ? { 'text/markdown': `${page.url}.mdx` } : undefined,
    },
    openGraph: {
      title,
      description,
      type: 'website',
      siteName: 'SwB',
      url,
      locale: { en: 'en_US', 'zh-Hans': 'zh_CN', 'zh-Hant': 'zh_TW', es: 'es_ES', fr: 'fr_FR', de: 'de_DE', ko: 'ko_KR', ja: 'ja_JP' }[lang],
      images: [{ url: image, alt: 'SwB', width: 1200, height: 630 }],
    },
    twitter: { card: 'summary_large_image', title, description, images: [image] },
  };
}
