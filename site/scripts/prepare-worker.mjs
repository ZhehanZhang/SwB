import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';
import { docsAssetPath, prerenderedDocsRoutes } from '../lib/docs-asset-path.ts';
import { locales } from '../lib/i18n.ts';

const { routes, dynamicRoutes } = JSON.parse(readFileSync('.next/prerender-manifest.json', 'utf8'));

for (const [name, path] of Object.entries(prerenderedDocsRoutes)) {
  const entries = Object.values(routes).filter(({ srcRoute }) => srcRoute === path);
  if (entries.length === 0 || entries.some(({ compute, response, initialRevalidateSeconds }) =>
    compute !== 'static' || response !== 'complete' || initialRevalidateSeconds !== false)
    || (path.includes('[') && dynamicRoutes[path]?.fallback !== false)) {
    throw new Error(`The Worker requires immutable prerenders for ${name}`);
  }
}

mkdirSync(`.open-next/assets${docsAssetPath}`, { recursive: true });
for (const name of [...locales, 'search']) {
  const path = `${docsAssetPath}/${name}.json`;
  copyFileSync(`.next/server/app${path}.body`, `.open-next/assets${path}`);
}
