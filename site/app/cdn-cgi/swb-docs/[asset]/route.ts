import type { SearchSource } from '@/lib/docs-assets';
import { isLocale, locales } from '@/lib/i18n';
import { docsSource, getLLMText, source } from '@/lib/source';

export const dynamic = 'force-static';
export const dynamicParams = false;

export function generateStaticParams() {
  return [...locales, 'search'].map((name) => ({ asset: `${name}.json` }));
}

export async function GET(_: Request, { params }: RouteContext<'/cdn-cgi/swb-docs/[asset]'>) {
  const { asset } = await params;
  if (asset === 'search.json') {
    const files: SearchSource['files'] = docsSource.files.map(({ type, path, data }) => {
      if (type === 'meta') return { type, path, data };
      const { title, description, structuredData } = data;
      return { type, path, data: { title, description, structuredData } };
    });
    return Response.json({ files });
  }
  const lang = asset.slice(0, -5);
  if (!asset.endsWith('.json') || !isLocale(lang)) return new Response(null, { status: 404 });
  return Response.json(await Promise.all(source.getPages(lang).map(async (page) => ({
    url: page.url,
    root: page.slugs.length === 0,
    text: await getLLMText(page),
  }))));
}
