import { NextResponse, type NextRequest } from 'next/server';

const choose = (header: string | null) => {
  for (const part of (header ?? '').toLowerCase().split(',')) {
    const tag = part.split(';')[0].trim();
    if (tag.startsWith('zh-hant') || tag === 'zh-tw' || tag === 'zh-hk' || tag === 'zh-mo') return 'zh-Hant';
    if (tag.startsWith('zh')) return 'zh-Hans';
    if (tag.startsWith('en')) return 'en';
  }
  return 'en';
};

export function middleware(request: NextRequest) {
  const saved = request.cookies.get('swb-lang')?.value;
  const lang = saved === 'en' || saved === 'zh-Hans' || saved === 'zh-Hant' ? saved : choose(request.headers.get('accept-language'));
  if (lang === 'en') return NextResponse.next();
  const url = request.nextUrl.clone();
  url.pathname = `/${lang}${url.pathname === '/' ? '' : url.pathname}`;
  const response = NextResponse.redirect(url, 307);
  response.headers.set('cache-control', 'private, no-store');
  response.headers.set('vary', 'accept-language, cookie');
  return response;
}

export const config = { matcher: ['/', '/docs', '/docs/:path*', '/about'] };
