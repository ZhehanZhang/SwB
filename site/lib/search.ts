import { createFromSource } from 'fumadocs-core/search/server';
import { loader } from 'fumadocs-core/source';
import { readDocsAsset, type SearchSource } from './docs-assets';
import { docsLoader } from './docs-loader';
import { docsAssetName, isDocsVersion, latestDocsVersion } from './docs-version';

async function loadSearch(version: string) {
  if (process.env.NODE_ENV === 'development') {
    const source = (await import('./source')).docsLoaders.get(version);
    return source && createFromSource(source);
  }
  const source = await readDocsAsset<SearchSource>(docsAssetName(version, 'search'));
  return source && createFromSource(loader(source, docsLoader(version)));
}

const searches = new Map<string, ReturnType<typeof loadSearch>>();

export async function search(request: Request) {
  const version = new URL(request.url).searchParams.get('version') ?? latestDocsVersion;
  if (!isDocsVersion(version)) return new Response(null, { status: 404 });
  let pending = searches.get(version);
  if (!pending) {
    pending = loadSearch(version).then((server) => {
      if (!server) searches.delete(version);
      return server;
    }, (error) => {
      searches.delete(version);
      throw error;
    });
    searches.set(version, pending);
  }
  const server = await pending;
  return server ? server.GET(request) : new Response(null, { status: 404 });
}
