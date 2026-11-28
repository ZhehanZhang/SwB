import { once } from 'node:events';
import { createServer, get, type ServerResponse } from 'node:http';
import { expect, test } from '@playwright/test';

const streamed = [
  ['caption streams', '<figcaption', ['.swb-hero', '.swb-board:visible'], '.swb-hero figcaption'],
  ['hero actions stream', '<div class="mt-6 grid', ['.swb-hero-copy'], '[data-hero-actions]'],
  ['demo label streams', '<span>Set this up</span>', ['.swb-win-setup'], '.swb-win-setup > span'],
] as const;

for (const [part, marker, stable, arriving] of streamed) test(`landing layout stays fixed when the ${part} after first paint`, async ({ page, request, baseURL }, info) => {
  test.skip(part === 'hero actions stream' && info.project.name === '390', 'The mobile layout does not vertically center the copy.');
  const response = await request.get('/');
  expect(response.ok()).toBe(true);
  const html = await response.text();
  const split = html.indexOf(marker, html.indexOf('<main'));
  expect(split).toBeGreaterThan(0);
  const upstream = new URL(baseURL!).origin;
  let documentResponse: ServerResponse | undefined;
  const server = createServer((incoming, outgoing) => {
    if (incoming.url === '/') {
      documentResponse = outgoing;
      outgoing.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      outgoing.write(html.slice(0, split));
      return;
    }
    get(upstream + incoming.url, (asset) => {
      outgoing.writeHead(asset.statusCode!, asset.headers);
      asset.pipe(outgoing);
    }).on('error', () => outgoing.destroy());
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Expected a TCP address');
    await page.goto(`http://127.0.0.1:${address.port}/`, { waitUntil: 'commit' });
    await page.waitForFunction(() => performance.getEntriesByName('first-contentful-paint').length > 0);

    const layout = stable.map((selector) => page.locator(selector).first());
    const pending = page.locator(arriving);
    for (const item of layout) await expect(item).toBeVisible();
    await expect(pending).toHaveCount(0);
    const initial = await Promise.all(layout.map((item) => item.boundingBox()));
    documentResponse!.end(html.slice(split));
    await expect(pending.first()).toBeVisible();
    await page.waitForLoadState();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(await Promise.all(layout.map((item) => item.boundingBox()))).toEqual(initial);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
