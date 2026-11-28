import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const base = process.argv[2] ?? 'https://swb.one';
const out = new URL('../public/og/', import.meta.url);
const locales = ['en', 'zh-Hans', 'zh-Hant', 'es', 'fr', 'de', 'ko', 'ja'];
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1, colorScheme: 'light', reducedMotion: 'reduce' });
for (const lang of locales) {
  await page.goto(`${base}/${lang === 'en' ? '' : lang}`, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: '#nd-nav { display: none !important; } [data-landing-section] { visibility: hidden; }' });
  await page.waitForTimeout(1500);
  const hero = page.locator('main section').first();
  const box = (await hero.boundingBox());
  await page.evaluate((y) => scrollTo(0, y), Math.max(0, box.y + box.height / 2 - 315));
  await page.waitForTimeout(300);
  await page.screenshot({ path: new URL(`hero-${lang.toLowerCase()}.png`, out).pathname });
}
await browser.close();
