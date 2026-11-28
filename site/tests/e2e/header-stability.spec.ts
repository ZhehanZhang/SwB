import { expect, test, type Page } from '@playwright/test';
import { observeLayoutShifts } from './layout-shifts';

const pages = ['/', '/docs/install', '/docs/about', '/zh-Hans', '/zh-Hans/docs/install'];
const items = ['search', 'address', 'language', 'github'];

async function headerRects(page: Page) {
  return page.evaluate((names) => Object.fromEntries(names.map((name) => {
    const rect = document.querySelector(`#nd-nav [data-header-item="${name}"]`)!.getBoundingClientRect();
    return [name, { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width) }];
  })), items);
}

test('the header controls keep one position on every page', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(observeLayoutShifts);
  const rects = [];
  for (const path of pages) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    rects.push(await headerRects(page));
    const shifts = await page.evaluate(() => (window as unknown as { swbLayoutShifts: { value: number; sources: { node?: string }[] }[] }).swbLayoutShifts
      .filter(({ sources }) => sources.some(({ node }) => node?.includes('data-header')))
      .reduce((sum, { value }) => sum + value, 0));
    expect(shifts, path).toBe(0);
  }
  for (const rect of rects.slice(1)) expect(rect).toEqual(rects[0]);
});
