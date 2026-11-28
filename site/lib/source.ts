import { docs, versions } from 'fumadocs-mdx:collections/server';
import { loader, type InferPageType } from 'fumadocs-core/source';
import { docsLoader } from './docs-loader';
import { isDocsVersion, latestDocsVersion, splitDocsSlug } from './docs-version';
import { siteUrl } from './urls';

export { repository, siteUrl } from './urls';

const docsSource = docs.toFumadocsSource();
export const source = loader(docsSource, docsLoader());

type VersionFiles = typeof docsSource.files;
const archived = new Map<string, VersionFiles>();
for (const file of versions.toFumadocsSource().files) {
  const match = /^(?:docs\/versions|site\/tests\/fixtures\/docs-versions)\/([^/]+)\/(.+)$/.exec(file.path);
  if (!match) throw new Error(`Invalid docs snapshot path ${file.path}`);
  const [, version, path] = match;
  if (!isDocsVersion(version) || version === latestDocsVersion) throw new Error(`Invalid docs snapshot version ${version}`);
  const files = archived.get(version) ?? [];
  if (files.length === 0) archived.set(version, files);
  files.push({ ...file, path });
}

export const docsSources = new Map([[latestDocsVersion, docsSource]]);
export const docsLoaders = new Map([[latestDocsVersion, source]]);
for (const [version, files] of [...archived].sort(([left], [right]) => right.localeCompare(left, 'en', { numeric: true }))) {
  const snapshot = { files };
  const versionLoader = loader(snapshot, docsLoader(version));
  for (const { language, pages } of versionLoader.getLanguages()) {
    if (pages.length > 0 && !versionLoader.getPage([], language)) {
      throw new Error(`Docs snapshot ${version} needs an index for ${language}`);
    }
  }
  docsSources.set(version, snapshot);
  docsLoaders.set(version, versionLoader);
}

export type DocsPage = InferPageType<typeof source>;

export function docsPage(slugs: string[] | undefined, lang: string) {
  const { version, slug } = splitDocsSlug(slugs);
  if (slugs?.[0] === `v${latestDocsVersion}`) return;
  const versionLoader = docsLoaders.get(version);
  const page = versionLoader?.getPage(slug, lang);
  if (!page || !versionLoader || (version === latestDocsVersion && page.slugs.join('/') === 'about')) return;
  return { version, page, versionLoader };
}

export function latestPageHref(slugs: string[]) {
  return (source.getPage(slugs, 'en') ?? source.getPage([], 'en'))?.url;
}

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
