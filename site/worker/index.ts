import { DurableObject } from 'cloudflare:workers';
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

export default handler;
