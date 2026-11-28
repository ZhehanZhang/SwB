import { once } from 'node:events';
import { createServer, get, type ServerResponse } from 'node:http';
import { expect, test } from '@playwright/test';

const streamed = [
  ['caption streams', '<figcaption', ['.swb-hero', '.swb-board:visible'], '.swb-hero figcaption', undefined],
  ['hero subtitle streams', '<p class="swb-between', ['.swb-hero-copy'], '.swb-between', undefined],
  ['hero actions stream', '<div class="swb-hero-controls"', ['.swb-hero-copy'], '[data-hero-actions]', undefined],
  ['hero actions stream at 1024px', '<div class="swb-hero-controls"', ['.swb-hero-copy'], '[data-hero-actions]', 1024],
  ['hero buttons stream', 'Set up with agent</a>', ['[data-hero-actions]'], '[data-hero-actions] a + a', undefined],
  ['demo label streams', '<span>Set this up</span>', ['.swb-win-setup'], '.swb-win-setup > span', undefined],
] as const;

for (const [part, marker, stable, arriving, width] of streamed) test(`landing layout stays fixed when the ${part} after first paint`, async ({ page, request, baseURL }) => {
  if (width) await page.setViewportSize({ width, height: 900 });
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
    const measure = async () => {
      const boxes = await Promise.all(layout.map((item) => item.boundingBox()));
      return part === 'hero buttons stream' ? boxes.map((box) => box && { x: box.x, y: box.y }) : boxes;
    };
    const initial = await measure();
    documentResponse!.end(html.slice(split));
    await expect(pending.first()).toBeVisible();
    await page.waitForLoadState();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(await measure()).toEqual(initial);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
