import { chromium } from '@playwright/test';

const base = process.argv[2] ?? 'https://swb.one';
const out = new URL('../public/og/swb-board.png', import.meta.url).pathname;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1, colorScheme: 'light', reducedMotion: 'reduce' });
await page.goto(base, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);
await page.waitForTimeout(1500);
const board = page.locator('.swb-board-wide');
const { width, height } = (await board.boundingBox());
const scale = Math.min(1180 / width, 690 / height);
await page.addStyleTag({ content: `
  html, body { overflow: hidden !important; background: var(--background, #fff) !important; }
  body * { visibility: hidden !important; }
  .swb-board-wide, .swb-board-wide * { visibility: visible !important; }
  .swb-node-label, .swb-hero figcaption { display: none !important; }
  .swb-board-wide { position: fixed !important; left: 50% !important; top: 50% !important; width: ${width}px !important; margin: 0 !important;
    transform: translate(-50%, -50%) scale(${scale}) !important; transform-origin: center !important; z-index: 9999; }
  .swb-transit[data-active] { stroke-width: 4px !important; }
  .swb-hub { scale: 1.45; transform-origin: 0 0 !important; }
` });
await page.waitForTimeout(500);
await page.screenshot({ path: out });
await browser.close();
