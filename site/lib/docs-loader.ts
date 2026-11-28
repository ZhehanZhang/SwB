import { createGetUrl } from 'fumadocs-core/source';
import { docsRoot, latestDocsVersion } from './docs-version';
import { i18n } from './i18n';

const aboutUrl = createGetUrl('/about', i18n);

export function docsLoader(version = latestDocsVersion) {
  const baseUrl = docsRoot(version);
  const docsUrl = createGetUrl(baseUrl, i18n);
  return {
    baseUrl,
    i18n,
    url: (slugs: string[], lang?: string) => version === latestDocsVersion && slugs.length === 1 && slugs[0] === 'about'
      ? aboutUrl([], lang)
      : docsUrl(slugs, lang),
  };
}
