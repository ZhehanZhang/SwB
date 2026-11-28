import { expect, test } from '@playwright/test';

const added = ['es', 'fr', 'de', 'ko', 'ja'] as const;
const names = ['English', '简体中文', '繁體中文', 'Español', 'Français', 'Deutsch', '한국어', '日本語'];
const widths = [390, 1024, 1440];

for (const [header, lang] of [['es-MX', 'es'], ['fr-CA', 'fr'], ['de-AT', 'de'], ['ko-KR', 'ko'], ['ja', 'ja'], ['ja-JP;q=0.5, ko;q=0.9', 'ko']] as const) {
  test(`Accept-Language ${header} opens /${lang}`, async ({ request }) => {
    const response = await request.get('/docs/install', { headers: { 'Accept-Language': header }, maxRedirects: 0 });
    expect(response.status()).toBe(307);
    expect(new URL(response.headers().location!, response.url()).pathname).toBe(`/${lang}/docs/install`);
  });
}

test('the language menu lists every locale by its native name', async ({ page }) => {
  await page.goto('/ja/docs');
  await page.locator('[data-header-item="language"]').click();
  const menu = page.getByRole('dialog');
  for (const name of names) await expect(menu.getByRole('button', { name, exact: true })).toBeVisible();
  await menu.getByRole('button', { name: 'Deutsch', exact: true }).click();
  await expect(page).toHaveURL(/\/de\/docs$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
});

test('docs pages in added locales carry the translation note and the landing pages do not', async ({ page }) => {
  for (const lang of added) {
    await page.goto(`/${lang}`);
    await expect(page.locator('[data-translation-note]')).toHaveCount(0);
    for (const path of ['/docs', '/docs/setup/mcp-client', '/about']) {
      await page.goto(`/${lang}${path}`);
      const note = page.locator('[data-translation-note]');
      await expect(note).toHaveCount(1);
      await expect(note.locator('a[href*="CONTRIBUTING.md#proofread-translations"]')).toHaveCount(1);
      await expect(note.locator('a[href*="/issues/new"]')).toHaveCount(1);
    }
  }
  for (const path of ['/docs', '/zh-Hans/docs', '/zh-Hant/docs']) {
    await page.goto(path);
    await expect(page.locator('[data-translation-note]')).toHaveCount(0);
  }
});

test('added locales link every language alternate', async ({ page }) => {
  await page.goto('/ko/docs/install');
  const alternates = await page.locator('link[rel="alternate"][hreflang]').evaluateAll((links) => links.map((link) => link.getAttribute('hreflang')));
  expect(alternates.sort()).toEqual(['de', 'en', 'es', 'fr', 'ja', 'ko', 'x-default', 'zh-Hans', 'zh-Hant']);
});

for (const lang of added) {
  test(`${lang} header, hero, buttons and rows fit at every width`, async ({ page }) => {
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      for (const path of [`/${lang}`, `/${lang}/docs/install`]) {
        await page.goto(path, { waitUntil: 'networkidle' });
        await page.evaluate(() => document.fonts.ready);
        const at = `${path} at ${width}px`;
        const clipped = await page.evaluate(() => [...document.querySelectorAll('#nd-nav [data-header-item], #nd-nav button, .swb-button, .swb-chip-on, main h1, main h2')]
          .filter((node) => (node as HTMLElement).offsetParent && (node.getBoundingClientRect().right > window.innerWidth + 0.5 || node.getBoundingClientRect().left < -0.5 || node.scrollWidth > node.clientWidth + 1 || (node.matches('#nd-nav button') && node.scrollHeight > node.clientHeight + 1)))
          .map((node) => node.textContent?.trim().slice(0, 40)));
        expect(clipped, at).toEqual([]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), at).toBe(true);
        const header = await page.locator('#nd-nav').boundingBox();
        await page.goto(path.replace(`/${lang}`, '') || '/');
        await page.evaluate(() => document.fonts.ready);
        await expect(page.locator('#nd-nav')).toBeVisible();
        expect(Math.abs((await page.locator('#nd-nav').boundingBox())!.height - header!.height), `${at} header height`).toBeLessThanOrEqual(0.5);
      }
    }
  });

  test(`${lang} hero endings stay on one line without orphaned glyphs`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.clock.install();
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/${lang}`);
      await page.evaluate(() => document.fonts.ready);
      const heading = page.locator('#landing-heading');
      const active = heading.locator('.swb-rotate-on');
      const count = await heading.locator('.swb-rotate-item').count();
      for (let frame = 0; frame < count; frame++) {
        const previous = await active.textContent();
        const lines = await heading.evaluate((node) => {
          const rows = (element: Element) => {
            const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
            const range = document.createRange();
            const found: { top: number; right: number; text: string }[] = [];
            for (let text = walker.nextNode(); text; text = walker.nextNode()) {
              let offset = 0;
              for (const char of text.textContent!) {
                range.setStart(text, offset);
                offset += char.length;
                range.setEnd(text, offset);
                const rect = range.getClientRects().item(0);
                if (!rect) continue;
                const row = found.find(({ top }) => Math.abs(top - rect.top) < rect.height / 2);
                if (row) { row.text += char; row.right = Math.max(row.right, rect.right); } else found.push({ top: rect.top, right: rect.right, text: char });
              }
            }
            return found.map(({ right, text }) => ({ right, text: text.trim() })).filter(({ text }) => text);
          };
          return { stem: rows(node.querySelector('[data-hero-stem]')!), ending: rows(node.querySelector('.swb-rotate-on')!), right: node.getBoundingClientRect().right };
        });
        const at = `${lang} at ${width}px ending ${previous}`;
        expect(lines.ending.length, at).toBe(1);
        for (const { text } of [...lines.stem, ...lines.ending]) expect([...text.replace(/[\s,，。、.]/g, '')].length, at).toBeGreaterThan(1);
        for (const { right } of [...lines.stem, ...lines.ending]) expect(right, at).toBeLessThanOrEqual(lines.right + 1);
        await page.clock.fastForward(2400);
        await expect(active).not.toHaveText(previous!);
      }
    }
  });
}
