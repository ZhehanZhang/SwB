import type { SearchSource } from '@/lib/docs-assets';
import { docsAssetName } from '@/lib/docs-version';
import { docsLoaders, docsSources, getLLMText } from '@/lib/source';

export const dynamic = 'force-static';
export const dynamicParams = false;

const assets = new Map([...docsLoaders].flatMap(([version, versionLoader]) =>
  [`search`, ...versionLoader.getLanguages().filter(({ pages }) => pages.length > 0).map(({ language }) => language)]
    .map((name) => [docsAssetName(version, name), { version, versionLoader, name }] as const),
));

export function generateStaticParams() {
  return [...assets.keys()].map((asset) => ({ asset }));
}

export async function GET(_: Request, { params }: RouteContext<'/cdn-cgi/swb-docs/[asset]'>) {
  const { asset } = await params;
  const entry = assets.get(asset);
  if (!entry) return new Response(null, { status: 404 });
  const { version, versionLoader, name } = entry;
  if (name === 'search') {
    const files: SearchSource['files'] = docsSources.get(version)!.files.map(({ type, path, data }) => {
      if (type === 'meta') return { type, path, data };
      const { title, description, structuredData } = data;
      return { type, path, data: { title, description, structuredData } };
    });
    return Response.json({ files });
  }
  return Response.json(await Promise.all(versionLoader.getPages(name).map(async (page) => ({
    url: page.url,
    root: page.slugs.length === 0,
    text: await getLLMText(page),
  }))));
}
