import { docs } from 'fumadocs-mdx:collections/server';
import { createGetUrl, loader, type InferPageType } from 'fumadocs-core/source';
import { i18n } from './i18n';

const docsUrl = createGetUrl('/docs', i18n);
const aboutUrl = createGetUrl('/about', i18n);

export const source = loader(docs.toFumadocsSource(), {
  baseUrl: '/docs',
  i18n,
  url: (slugs, lang) => slugs.length === 1 && slugs[0] === 'about' ? aboutUrl([], lang) : docsUrl(slugs, lang),
});

export type DocsPage = InferPageType<typeof source>;

export const siteUrl = 'https://swb.one';
export const repository = 'https://github.com/ZhehanZhang/SwB';

export function markdownUrl(page: DocsPage) {
  return `${page.url}.mdx`;
}

export async function getLLMText(page: DocsPage) {
  return `# ${page.data.title}\n\nURL: ${siteUrl}${page.url}\n\n${await page.data.getText('processed')}`;
}

export function markdownResponse(text: string, type = 'text/markdown') {
  return new Response(text, { headers: { 'Content-Type': `${type}; charset=utf-8` } });
}

export async function fullMarkdown(lang: string) {
  return (await Promise.all(source.getPages(lang).map(getLLMText))).join('\n\n---\n\n');
}
