import { once } from 'node:events';
import { createServer, get, type ServerResponse } from 'node:http';
import { expect, test } from '@playwright/test';
import { observeLayoutShifts, readLayoutShifts } from './layout-shifts';

test('the landing diagram stays fixed when its caption streams after first paint', async ({ page, request, baseURL }) => {
  const response = await request.get('/');
  expect(response.ok()).toBe(true);
  const html = await response.text();
  const split = html.indexOf('<figcaption');
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
    await page.addInitScript(observeLayoutShifts);
    await page.goto(`http://127.0.0.1:${address.port}/`, { waitUntil: 'commit' });
    await page.waitForFunction(() => performance.getEntriesByName('first-contentful-paint').length > 0);

    const figure = page.locator('.swb-hero');
    const board = figure.locator('.swb-board:visible');
    const caption = figure.locator('figcaption');
    await expect(board).toBeVisible();
    await expect(caption).toHaveCount(0);
    const initialFigure = await figure.boundingBox();
    const initialBoard = await board.boundingBox();
    documentResponse!.end(html.slice(split));
    await expect(caption).toBeVisible();
    await page.waitForLoadState();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(await figure.boundingBox()).toEqual(initialFigure);
    expect(await board.boundingBox()).toEqual(initialBoard);
    const shifts = await page.evaluate(readLayoutShifts);
    expect(shifts.reduce((total, shift) => total + shift.value, 0), JSON.stringify(shifts, null, 2)).toBe(0);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
