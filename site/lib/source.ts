import { docs } from 'fumadocs-mdx:collections/server';
import { loader, type InferPageType } from 'fumadocs-core/source';
import { docsLoader } from './docs-loader';
import { siteUrl } from './urls';

export { repository, siteUrl } from './urls';

export const docsSource = docs.toFumadocsSource();
export const source = loader(docsSource, docsLoader);

export type DocsPage = InferPageType<typeof source>;

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
