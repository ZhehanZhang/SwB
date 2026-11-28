import { readFile } from 'node:fs/promises';
import { getCloudflareContext } from '@opennextjs/cloudflare';
import type { AdvancedIndex } from 'fumadocs-core/search/server';
import type { MetaData, PageData, StaticSource } from 'fumadocs-core/source';
import { docsAssetPath } from './docs-asset-path';

export type SearchSource = StaticSource<{
  pageData: PageData & Pick<AdvancedIndex, 'structuredData'>;
  metaData: MetaData;
}>;

export type AskPage = { url: string; root: boolean; text: string };

export async function readDocsAsset<T>(name: string): Promise<T | undefined> {
  let assets: CloudflareEnv['ASSETS'] | undefined;
  try {
    assets = getCloudflareContext().env.ASSETS;
  } catch {}

  const path = `${docsAssetPath}/${name}`;
  if (!assets) {
    try {
      return JSON.parse(await readFile(`.next/server/app${path}.body`, 'utf8'));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      throw error;
    }
  }

  const response = await assets.fetch(`http://assets.local${path}`);
  if (response.status === 404) return;
  if (!response.ok) throw new Error(`Docs asset ${name} returned HTTP ${response.status}`);
  return response.json();
}
