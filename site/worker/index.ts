import { DurableObject } from 'cloudflare:workers';
import { docsAssetPath } from '../lib/docs-asset-path';
import { locales } from '../lib/i18n';
// @ts-ignore The OpenNext build writes this file.
import handler from '../.open-next/worker.js';

export class AskLimiter extends DurableObject {
  async fetch(request: Request) {
    const { searchParams } = new URL(request.url);
    const limit = Number(searchParams.get('limit'));
    const period = Number(searchParams.get('period')) * 1000;
    const now = Date.now();
    const start = (await this.ctx.storage.get<number>('start')) ?? 0;
    const count = now - start >= period ? 0 : (await this.ctx.storage.get<number>('count')) ?? 0;
    if (count >= limit) return new Response(null, { status: 429 });
    await this.ctx.storage.put(count === 0 ? { start: now, count: 1 } : { count: count + 1 });
    return new Response(null, { status: 204 });
  }
}

const localePrefix = new RegExp(`^/(?:${locales.join('|')})(?=/)`);
const methodRestricted = new Set(['/llms.txt', '/llms-full.txt', '/en/llms-full.txt', '/sitemap.xml']);

export default {
  async fetch(request: Request, env: CloudflareEnv, ctx: ExecutionContext) {
    const { pathname } = new URL(request.url);
    if (pathname.startsWith(`${docsAssetPath}/`)) {
      return new Response(null, { status: 404 });
    }
    if (request.method === 'GET' || request.method === 'HEAD') return handler.fetch(request, env, ctx);
    if (methodRestricted.has(pathname)) {
      return new Response(new TextEncoder().encode('Method Not Allowed'), { status: 405, headers: { Allow: 'GET,HEAD' } });
    }

    const path = pathname.replace(/\/$/, '').replace(localePrefix, '');
    if (!(path === '/docs' || path.startsWith('/docs/') || path === '/docs.mdx' || path === '/about'
      || path === '/about.mdx' || path === '/llms-full.txt' || pathname.startsWith('/llms.mdx/'))) {
      return handler.fetch(request, env, ctx);
    }

    const headers = new Headers(request.headers);
    headers.set('accept-language', 'en');
    headers.delete('cookie');
    const response: Response = await handler.fetch(new Request(request.url, { headers, signal: request.signal }), env, ctx);
    if (request.method === 'OPTIONS' && response.headers.get('content-type')?.startsWith('text/html')) {
      await response.body?.cancel();
      return new Response(null, { status: 400 });
    }
    return response;
  },
};
