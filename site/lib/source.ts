import { docs } from 'fumadocs-mdx:collections/server';
import { createGetUrl, loader, type InferPageType } from 'fumadocs-core/source';
import { i18n } from './i18n';
import { siteUrl } from './urls';

export { repository, siteUrl } from './urls';

const docsUrl = createGetUrl('/docs', i18n);
const aboutUrl = createGetUrl('/about', i18n);

export const source = loader(docs.toFumadocsSource(), {
  baseUrl: '/docs',
  i18n,
  url: (slugs, lang) => slugs.length === 1 && slugs[0] === 'about' ? aboutUrl([], lang) : docsUrl(slugs, lang),
});

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

export async function askContext(lang: string, question: string, location: string | undefined, budget: number) {
  const terms = [...new Set(question.toLowerCase().match(/[a-z0-9_.-]{3,}|[\u3400-\u9fff]{2}/g) ?? [])];
  const pages = await Promise.all(source.getPages(lang).map(async (page) => {
    const text = await getLLMText(page);
    const lower = text.toLowerCase();
    const hits = terms.reduce((sum, term) => sum + Math.min(lower.split(term).length - 1, 20), 0);
    return { text, score: hits + (page.url === location ? 1000 : 0) + (page.slugs.length === 0 ? 5 : 0) };
  }));
  const picked: string[] = [];
  let used = 0;
  for (const { text } of pages.sort((a, b) => b.score - a.score)) {
    const part = text.slice(0, budget - used);
    if (part.length < 200) break;
    picked.push(part);
    used += part.length;
  }
  return picked.join('\n\n---\n\n');
}

export async function fullMarkdown(lang: string) {
  return (await Promise.all(source.getPages(lang).map(getLLMText))).join('\n\n---\n\n');
}
