import { createFromSource } from 'fumadocs-core/search/server';
import { loader } from 'fumadocs-core/source';
import { readDocsAsset, type SearchSource } from './docs-assets';
import { docsLoader } from './docs-loader';

async function loadSearchSource() {
  if (process.env.NODE_ENV === 'development') return (await import('./source')).source;
  return loader(await readDocsAsset<SearchSource>('search.json'), docsLoader);
}

let source: ReturnType<typeof loadSearchSource> | undefined;
export const search = createFromSource(() => source ??= loadSearchSource().catch((error) => {
  source = undefined;
  throw error;
}));
