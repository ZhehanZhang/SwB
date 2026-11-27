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
    const win = section.querySelector('.swb-win')!;
    const messages = section.querySelectorAll('.swb-win-message');
    return {
      size: { x, y, width, height, scrollHeight: document.documentElement.scrollHeight },
      windows: section.querySelectorAll('.swb-win').length,
      title: section.querySelector('.swb-win-bar')?.textContent,
      messages: messages.length,
      transcript: [...messages].map((message) => message.querySelector('p:last-child')?.textContent),
      bottomGap: win.getBoundingClientRect().bottom - messages[messages.length - 1].getBoundingClientRect().bottom,
      botMarks: section.querySelectorAll('.swb-win-message .swb-win-bot').length,
    };
  });

  const baseline = (await sample()).size;
  const titles: (string | undefined)[] = [];
  const messagesByTitle = new Map<string | undefined, Set<number>>();
  const deadline = Date.now() + 45_000;
  do {
    const { windows, size, title, messages, transcript, botMarks, bottomGap } = await sample();
    expect(windows).toBe(1);
    expect(size).toEqual(baseline);
    expect(bottomGap).toBeGreaterThanOrEqual(0);
    expect(bottomGap).toBeLessThanOrEqual(24);
    if (titles.at(-1) !== title) titles.push(title);
    const counts = messagesByTitle.get(title) ?? new Set<number>();
    counts.add(messages);
    messagesByTitle.set(title, counts);
    if (title === 'Slack #launch') expect(botMarks).toBe(messages - 1);
    if (title === 'Muse') {
      expect(transcript).toEqual([
        'Check my SwB inbox.',
        'Grok Bot replied. It found a Slack example for your talk.',
        'I used send to ask Grok Bot for a second example.',
      ].slice(0, messages));
    }
    if (titles.length === 5) break;
    await page.waitForTimeout(150);
  } while (Date.now() < deadline);

  expect(titles).toEqual(['Claude', 'Muse', 'Grok Bot', 'Slack #launch', 'Claude']);
  for (const [title, count] of [['Claude', 3], ['Muse', 3], ['Grok Bot', 5], ['Slack #launch', 4]] as const) {
    expect([...messagesByTitle.get(title) ?? []].sort()).toEqual(Array.from({ length: count }, (_, i) => i + 1));
  }
});

test('each demo window uses its owner and flags only relayed senders', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/');

  const win = page.locator('.swb-win');
  const header = win.locator('.swb-win-bar');
  const messages = win.locator('.swb-win-message');
  const scenes = [
    { title: 'Claude', owner: '/avatars/claude.png', senders: ['Claude', 'Grok Bot', 'Claude'], relayed: [1] },
    { title: 'Muse', owner: '/avatars/muse.png', senders: ['You', 'Muse', 'Muse'], relayed: [] },
    { title: 'Grok Bot', owner: '/avatars/grok-bot.png', senders: ['Grok Bot', 'Grok Bot', 'Claude', 'Capy Captain', 'Grok Bot'], relayed: [2, 3] },
    { title: 'Slack #launch', owner: null, senders: ['You', 'Claude', 'Grok Bot', 'Capy Captain'], relayed: [1, 2, 3] },
  ];
  const marks: Record<string, string> = {
    Claude: '/avatars/claude.png',
    Muse: '/avatars/muse.png',
    'Grok Bot': '/avatars/grok-bot.png',
    'Capy Captain': '/marks/captain.webp',
  };

  for (const { title, owner, senders, relayed } of scenes) {
    await expect(header).toHaveText(title);
    const ownerMark = header.locator('.swb-win-avatar');
    if (owner) await expect(ownerMark.locator('img')).toHaveAttribute('src', owner);
    else await expect(ownerMark.locator('.swb-mark-glyph')).toHaveCSS('mask-image', /\/marks\/slack\.svg/);

    for (const [index, sender] of senders.entries()) {
      if (index) await page.clock.fastForward(1250);
      await expect(messages).toHaveCount(index + 1);
      const message = messages.nth(index);
      const avatar = message.locator('.swb-win-avatar');
      const name = message.locator('p').first();
      await expect(name).toHaveText(sender);
      if (sender === 'You') {
        await expect(avatar.locator('svg')).toHaveClass(/lucide-user-round/);
        await expect(avatar.locator('img')).toHaveCount(0);
      } else {
        await expect(avatar.locator('img')).toHaveAttribute('src', marks[sender]);
      }

      const badge = name.locator('.swb-win-bot');
      if (relayed.includes(index)) {
        await expect(badge).toHaveAttribute('src', '/avatars/swb.png');
        await expect(badge).toHaveCSS('width', '14px');
      } else {
        await expect(badge).toHaveCount(0);
      }
    }

    const frames = await win.locator('.swb-win-bar .swb-win-avatar, .swb-win-message .swb-win-avatar').evaluateAll(
      (avatars) => avatars.map((avatar) => {
        const style = getComputedStyle(avatar);
        return [style.width, style.height, style.borderRadius, style.borderTopWidth, style.borderTopColor];
      }),
    );
    expect(frames[0].slice(0, 4)).toEqual(['32px', '32px', '50%', '1px']);
    expect(frames).toEqual(Array.from({ length: senders.length + 1 }, () => frames[0]));
    await expect.poll(() => win.locator('img').evaluateAll(
      (images: HTMLImageElement[]) => images.every((image) => image.complete && image.naturalWidth > 0),
    )).toBe(true);
    await page.clock.fastForward(3600);
  }
});

test('the continuous demo fills the top from the first frame through a full cycle', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const win = page.locator('.swb-win');
  await expect(win).toHaveAttribute('aria-hidden', 'true');
  const titles: string[] = [];
  let previousMessage: string | null | undefined;

  for (let i = 0; i < 130; i++) {
    const { title, gap, messages } = await win.evaluate((node) => {
      const header = node.querySelector('.swb-win-bar')!;
      const headerBottom = header.getBoundingClientRect().bottom;
      const entries = [...node.querySelectorAll('.swb-win-entry')];
      const first = entries.map((entry) => entry.getBoundingClientRect()).find(({ bottom }) => bottom > headerBottom);
      return {
        title: header.textContent!,
        gap: first ? Math.max(first.top, headerBottom) - headerBottom : Infinity,
        messages: entries.map((entry) => entry.textContent),
      };
    });
    expect(gap, `blank space below the ${title} header`).toBeLessThanOrEqual(24);
    if (titles.at(-1) !== title) {
      if (titles.length) expect(messages.at(-2)).toBe(previousMessage);
      else expect(messages.at(-2)).toContain('I opened pull request 42.');
      titles.push(title);
      if (titles.length === 5) break;
    }
    previousMessage = messages.at(-1);
    await page.clock.fastForward(250);
    await page.evaluate(() => new Promise(requestAnimationFrame));
  }
  expect(titles).toEqual(['Claude', 'Muse', 'Grok Bot', 'Slack #launch', 'Claude']);
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
  await expect(demo.locator('.swb-win-entry:visible')).toHaveCount(3);
  const body = demo.locator('.swb-win-body');
  expect(await body.evaluate((node) => {
    const { top, bottom } = node.getBoundingClientRect();
    return [...node.querySelectorAll('.swb-win-message')].every((entry) => {
      const rect = entry.getBoundingClientRect();
      return rect.top >= top && rect.bottom <= bottom;
    });
  })).toBe(true);
  expect(await demo.ariaSnapshot()).toContain('I will add the Docker command to the release notes.');
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
