import { expect, test, type Locator } from '@playwright/test';

const labels = [
  ['', 'Copy Markdown', 'Open in', 'Ask AI'],
  ['/zh-Hans', '复制 Markdown', '打开方式', '询问 AI'],
  ['/zh-Hant', '複製 Markdown', '開啟方式', '詢問 AI'],
  ['/es', 'Copiar Markdown', 'Abrir en', 'Preguntar a la IA'],
  ['/fr', 'Copier le Markdown', 'Ouvrir dans', "Demander à l'IA"],
  ['/de', 'Markdown kopieren', 'Öffnen in', 'KI fragen'],
  ['/ko', 'Markdown 복사', '다음에서 열기', 'AI에게 묻기'],
  ['/ja', 'Markdown をコピー', '開く', 'AI に質問'],
] as const;

const appearance = (button: Locator) => button.evaluate((node) => {
  const css = getComputedStyle(node);
  return {
    background: css.backgroundColor,
    border: css.borderTop,
    radius: css.borderRadius,
    font: css.fontFamily,
    fontSize: css.fontSize,
    weight: css.fontWeight,
    lineHeight: css.lineHeight,
    color: css.color,
    height: css.height,
    padding: css.padding,
    gap: css.gap,
  };
});

for (const [prefix, copyLabel, openLabel, askLabel] of labels) {
  test(`${prefix || 'en'} docs actions use the same family and share one row`, async ({ page }) => {
    await page.goto(`${prefix}/docs/get-started`);
    await page.evaluate(() => document.fonts.ready);
    const row = page.locator('[data-docs-actions]');
    const copy = row.getByRole('button', { name: copyLabel, exact: true });
    const open = row.getByRole('button', { name: openLabel, exact: true });
    const ask = row.getByRole('button', { name: askLabel, exact: true });
    const sidebar = row.locator('button[aria-controls^="nd-sidebar"]:visible');
    await expect(page.getByRole('button', { name: askLabel, exact: true })).toHaveCount(1);
    await expect(ask).toHaveCSS('font-size', '0px');
    await expect(sidebar).toHaveAccessibleName(/.+/);
    const [rowBox, ...boxes] = await Promise.all([row, copy, open, sidebar, ask].map((item) => item.boundingBox()));
    expect(boxes.every(Boolean)).toBe(true);
    for (const [index, box] of boxes.entries()) {
      expect(box!.y).toBe(boxes[0]!.y);
      expect(box!.height).toBe(36);
      expect(box!.x).toBeGreaterThanOrEqual(index === 0 ? rowBox!.x : boxes[index - 1]!.x + boxes[index - 1]!.width);
      expect(box!.x + box!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width + 0.5);
    }
    const labelThenChevron = await open.evaluate((node) => {
      const label = [...node.childNodes].find((child) => child.nodeType === Node.TEXT_NODE && child.textContent?.trim())!;
      const range = document.createRange();
      range.selectNodeContents(label);
      return range.getBoundingClientRect().right < node.querySelector('.lucide-chevron-down')!.getBoundingClientRect().left;
    });
    expect(labelThenChevron).toBe(true);

    for (const colorScheme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme });
      await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${colorScheme}\\b`));
      await page.mouse.move(1, 1);
      const idle = await appearance(copy);
      expect(await appearance(open)).toEqual(idle);
      const states = [];
      for (const button of [copy, open]) {
        await button.hover();
        const hover = await appearance(button);
        await page.mouse.down();
        const press = await appearance(button);
        await page.mouse.move(1, 1);
        await page.mouse.up();
        states.push([hover, press]);
      }
      expect(states[0]).toEqual(states[1]);
      expect(new Set([idle.background, states[0][0].background, states[0][1].background]).size).toBe(3);
    }
    if (page.viewportSize()!.width === 390) {
      await page.setViewportSize({ width: 320, height: 844 });
      await expect.poll(() => row.evaluate((node) => {
        const buttons = [...node.querySelectorAll('button')].filter((button) => button.offsetHeight > 0);
        const bounds = node.getBoundingClientRect();
        return buttons.length === 4 && buttons.every((button) => {
          const box = button.getBoundingClientRect();
          return box.top === buttons[0].getBoundingClientRect().top && box.right <= bounds.right + 0.5
            && button.scrollWidth <= button.clientWidth + 1
            && button.scrollHeight <= button.clientHeight + 1;
        });
      })).toBe(true);
    }
  });
}
