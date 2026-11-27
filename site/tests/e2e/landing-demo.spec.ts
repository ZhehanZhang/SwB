import { expect, test } from '@playwright/test';

test('one demo window keeps its size through a full cycle', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const demo = page.locator('.swb-exchange');
  await expect(demo.locator('.swb-win-bar')).toHaveText('Claude');
  await expect(demo.locator('button, [role="tablist"]')).toHaveCount(0);

  const sample = () => demo.evaluate((section) => {
    const { x, y, width, height } = section.getBoundingClientRect();
    return {
      size: { x, y, width, height, scrollHeight: document.documentElement.scrollHeight },
      windows: section.querySelectorAll('.swb-win').length,
      title: section.querySelector('.swb-win-bar')?.textContent,
      messages: section.querySelectorAll('.swb-win-message').length,
      botMarks: section.querySelectorAll('.swb-win-bot').length,
    };
  });

  const baseline = (await sample()).size;
  const titles: (string | undefined)[] = [];
  const messagesByTitle = new Map<string | undefined, Set<number>>();
  const deadline = Date.now() + 45_000;
  do {
    const { windows, size, title, messages, botMarks } = await sample();
    expect(windows).toBe(1);
    expect(size).toEqual(baseline);
    if (titles.at(-1) !== title) titles.push(title);
    const counts = messagesByTitle.get(title) ?? new Set<number>();
    counts.add(messages);
    messagesByTitle.set(title, counts);
    if (title === 'Slack #launch') expect(botMarks).toBe(messages - 1);
    if (titles.length === 5) break;
    await page.waitForTimeout(150);
  } while (Date.now() < deadline);

  expect(titles).toEqual(['Claude', 'Muse', 'Grok Bot', 'Slack #launch', 'Claude']);
  for (const [title, count] of [['Claude', 3], ['Muse', 3], ['Grok Bot', 5], ['Slack #launch', 4]] as const) {
    expect([...messagesByTitle.get(title) ?? []].sort()).toEqual(Array.from({ length: count }, (_, i) => i + 1));
  }
});

test('reduced motion keeps the diagram and demo still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');

  const board = page.locator('.swb-board:visible');
  const app = board.getByRole('button', { name: 'Claude', exact: true });
  await expect(app).toHaveCSS('animation-name', 'none');
  const cordImage = () => board.locator('canvas').evaluate((canvas: HTMLCanvasElement) => canvas.toDataURL());
  await expect.poll(async () => {
    return board.locator('canvas').evaluate((canvas: HTMLCanvasElement) => {
      const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
      return pixels.some((value, index) => index % 4 === 3 && value > 0);
    });
  }).toBe(true);
  const cords = await cordImage();

  const demo = page.locator('.swb-exchange');
  const messages = demo.locator('.swb-win-message');
  await expect(messages).toHaveCount(3);
  await expect(messages.first()).toHaveCSS('animation-name', 'none');
  const text = await demo.textContent();
  await page.waitForTimeout(1400);
  expect(await demo.textContent()).toBe(text);
  expect(await cordImage()).toBe(cords);

  await app.click();
  const setup = page.getByRole('dialog', { name: 'Claude' });
  await expect(setup.getByRole('button', { name: 'Copy prompt' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(setup).toBeHidden();
  await expect(app).toBeFocused();
});
