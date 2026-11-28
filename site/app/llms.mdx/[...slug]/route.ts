import { getLLMText, markdownResponse, source } from '@/lib/source';

export const dynamic = 'force-static';
export const dynamicParams = false;

export function generateStaticParams() {
  return source.getPages().map((page) => ({
    slug: page.url.slice(1).split('/').map(decodeURIComponent),
  }));
}

export async function GET(_: Request, context: RouteContext<'/llms.mdx/[...slug]'>) {
  const { slug } = await context.params;
  const path = `/${slug.join('/')}`;
  const page = source.getPages().find(({ url }) => decodeURI(url) === path);
  return page ? markdownResponse(await getLLMText(page)) : new Response(null, { status: 404 });
}
