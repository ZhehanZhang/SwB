import { expect, test } from '@playwright/test';

test('the language dropdown shows native names and saves the selection', async ({ page, context }) => {
  await page.setExtraHTTPHeaders({ 'Accept-Language': 'zh-TW' });
  await page.goto('/zh-Hans/docs/install?from=language#install-and-start');
  const trigger = page.locator('[data-header-item="language"]');
  await expect(trigger.locator('svg.lucide-languages')).toHaveCount(1);
  await trigger.click();
  const menu = page.getByRole('dialog');
  for (const name of ['English', '简体中文', '繁體中文']) {
    await expect(menu.getByRole('button', { name, exact: true })).toBeVisible();
  }
  await menu.getByRole('button', { name: '繁體中文', exact: true }).click();
  await expect(page).toHaveURL(/\/zh-Hant\/docs\/install\?from=language#install-and-start$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-Hant');
  const cookie = (await context.cookies()).find(({ name }) => name === 'swb-locale');
  expect(cookie).toMatchObject({ value: 'zh-Hant', path: '/', sameSite: 'Lax' });
  expect(cookie!.expires - Date.now() / 1000).toBeGreaterThan(31_535_900);

  await page.waitForLoadState('load');
  await expect(async () => {
    if (!(await menu.isVisible())) await trigger.click();
    await expect(menu).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 5000 });
  await menu.getByRole('button', { name: 'English', exact: true }).click();
  await expect(page).toHaveURL(/\/docs\/install\?from=language#install-and-start$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  expect((await context.cookies()).find(({ name }) => name === 'swb-locale')?.value).toBe('en');
});

test('localized shared navigation fits at 390px, 1024px and 1440px', async ({ page }) => {
  for (const path of ['/', '/zh-Hans', '/zh-Hant']) {
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      const entries = await page.locator('#nd-nav [data-header-item]:visible').evaluateAll((links) => links.map((link) => {
        const box = link.getBoundingClientRect();
        return box.left >= 0 && box.right <= window.innerWidth && link.scrollWidth <= link.clientWidth + 1;
      }));
      expect(entries.every(Boolean), `${path} at ${width}px`).toBe(true);
      await page.locator('[data-header-item="language"]').click();
      const options = page.getByRole('dialog').getByRole('button');
      await expect(options).toHaveCount(3);
      for (const option of await options.all()) {
        expect(await option.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
      }
    }
  }
});

for (const [lang, stem] of [
  ['zh-Hans', '第一方智能体协同工作，'], ['zh-Hant', '第一方代理協同工作，'],
] as const) {
  test(`${lang} keeps 不必 with every ending and avoids orphaned headline glyphs`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.clock.install();
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/${lang}`);
      await page.evaluate(() => document.fonts.ready);
      const heading = page.locator('#landing-heading');
      const active = heading.locator('.swb-rotate-on');
      const expected = await heading.locator('.swb-rotate-item').allTextContents();
      expect(expected).toHaveLength(6);
      const seen = new Set<string>();
      for (let frame = 0; frame < expected.length; frame++) {
        const previous = await active.textContent();
        const boxes = await heading.evaluate((node) => {
          const lines = (selector: string) => {
            const text = node.querySelector(selector)!.firstChild!;
            const range = document.createRange();
            const rows: { top: number; bottom: number; right: number; content: string }[] = [];
            let offset = 0;
            for (const char of text.textContent!) {
              range.setStart(text, offset);
              offset += char.length;
              range.setEnd(text, offset);
              const rect = range.getClientRects().item(0)!;
              const line = rows.find(({ top, bottom }) =>
                Math.min(bottom, rect.bottom) - Math.max(top, rect.top) > Math.min(bottom - top, rect.height) / 2);
              if (line) {
                line.content += char;
                line.top = Math.min(line.top, rect.top);
                line.bottom = Math.max(line.bottom, rect.bottom);
                line.right = Math.max(line.right, rect.right);
              } else rows.push({ top: rect.top, bottom: rect.bottom, right: rect.right, content: char });
            }
            return rows;
          };
          return { stem: lines('[data-hero-stem]'), ending: lines('.swb-rotate-on'), right: node.getBoundingClientRect().right };
        });
        const at = `${lang} at ${width}px ending ${previous}`;
        expect(boxes.stem.map(({ content }) => content), at).toEqual([stem]);
        expect(boxes.ending.map(({ content }) => content), at).toEqual([previous]);
        expect(previous, at).toMatch(/^不必./);
        expect(boxes.ending[0].top, at).toBeGreaterThan(boxes.stem[0].top);
        for (const { right } of [...boxes.stem, ...boxes.ending]) expect(right, at).toBeLessThanOrEqual(boxes.right + 1);
        seen.add(previous!);
        await page.clock.fastForward(2400);
        await expect(active).not.toHaveText(previous!);
      }
      expect([...seen].sort()).toEqual(expected.sort());
    }
  });
}
