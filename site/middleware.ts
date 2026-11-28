import { NextResponse, type NextRequest } from 'next/server';
import { isDocsVersion } from './lib/docs-version';
import { isLocale, localeCookie, localize, type Locale } from './lib/i18n';

const languageRange = /^((?:en|zh|es|fr|de|ko|ja)(?:-[a-z0-9]{1,8})*)(?:\s*;\s*q\s*=\s*(0(?:\.\d{0,3})?|1(?:\.0{0,3})?))?$/;
const automatedUserAgent = /bot|crawler|spider|python-urllib|\bcurl\b/i;

function preferredLocale(header: string | null): Locale {
  let locale: Locale = 'en';
  let weight = 0;
  for (const part of (header ?? '').split(',')) {
    const match = languageRange.exec(part.trim().toLowerCase());
    if (!match) continue;
    const [, tag, quality] = match;
    const candidateWeight = quality === undefined ? 1 : Number(quality);
    if (candidateWeight <= weight) continue;
    const [base, first, second] = tag.split('-');
    const region = first?.length === 4 ? second : first;
    const traditional = first === 'hant' || (first !== 'hans' && /^(tw|hk|mo)$/.test(region ?? ''));
    locale = base === 'zh' ? traditional ? 'zh-Hant' : 'zh-Hans' : base as Locale;
    weight = candidateWeight;
  }
  return locale;
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const versionRoot = pathname.match(/^\/docs\/v([^/]+)\/?$/)?.[1];
  if ((request.method !== 'GET' && request.method !== 'HEAD')
    || (/\.[^/]+\/?$/.test(pathname) && (!versionRoot || !isDocsVersion(versionRoot)))) {
    return NextResponse.next();
  }

  const saved = request.cookies.get(localeCookie)?.value;
  const lang = automatedUserAgent.test(request.headers.get('user-agent') ?? '')
    ? 'en'
    : saved && isLocale(saved) ? saved : preferredLocale(request.headers.get('accept-language'));
  let response = NextResponse.next();
  if (lang !== 'en') {
    const url = request.nextUrl.clone();
    url.pathname = localize(lang, pathname);
    response = NextResponse.redirect(url, 307);
  }
  response.headers.set('cache-control', 'private, no-store');
  response.headers.set('vary', 'Accept-Language, Cookie, User-Agent');
  return response;
}

export const config = { matcher: ['/', '/docs/:path*', '/about'] };
