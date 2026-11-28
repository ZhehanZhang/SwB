import { createGetUrl } from 'fumadocs-core/source';
import { i18n } from './i18n';

const docsUrl = createGetUrl('/docs', i18n);
const aboutUrl = createGetUrl('/about', i18n);

export const docsLoader = {
  baseUrl: '/docs',
  i18n,
  url: (slugs: string[], lang?: string) => slugs.length === 1 && slugs[0] === 'about'
    ? aboutUrl([], lang)
    : docsUrl(slugs, lang),
};
