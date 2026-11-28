import type { MetadataRoute } from 'next';
import { latestDocsVersion } from '@/lib/docs-version';
import { locales, localize } from '@/lib/i18n';
import { languageAlternates } from '@/lib/metadata';
import { docsLoaders, latestPageHref, siteUrl } from '@/lib/source';

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    ...locales.map((lang) => ({
      url: new URL(localize(lang, '/'), siteUrl).href,
      alternates: { languages: languageAlternates((lang) => localize(lang, '/')) },
    })),
    ...[...docsLoaders].flatMap(([version, versionLoader]) => versionLoader.getPages().map((page) => ({
      url: new URL(page.url, siteUrl).href,
      alternates: { languages: languageAlternates(
        (lang) => versionLoader.getPage(page.slugs, lang)?.url,
        version === latestDocsVersion ? undefined : latestPageHref(page.slugs),
      ) },
    }))),
  ];
}
