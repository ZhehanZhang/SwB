import { isLocale, locales } from '@/lib/i18n';
import { fullMarkdown, markdownResponse } from '@/lib/source';

export const dynamic = 'force-static';
export const dynamicParams = false;

export function generateStaticParams() {
  return locales.filter((lang) => lang !== 'en').map((lang) => ({ lang }));
}

export async function GET(_: Request, context: RouteContext<'/[lang]/llms-full.txt'>) {
  const { lang } = await context.params;
  if (!isLocale(lang) || lang === 'en') return new Response(null, { status: 404 });
  return markdownResponse(await fullMarkdown(lang), 'text/plain');
}
