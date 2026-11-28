import { defineCloudflareConfig } from '@opennextjs/cloudflare';
import staticAssetsIncrementalCache from '@opennextjs/cloudflare/overrides/incremental-cache/static-assets-incremental-cache';
import { prerenderedDocsRoutes } from './lib/docs-asset-path';

export default {
  ...defineCloudflareConfig({
    incrementalCache: staticAssetsIncrementalCache,
    enableCacheInterception: true,
  }),
  buildCommand: 'next build && node scripts/prepare-worker.mjs',
  functions: {
    prerendered: {
      routes: Object.keys(prerenderedDocsRoutes),
      patterns: [],
    },
  },
};
