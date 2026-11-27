import { expect, test } from '@playwright/test';
import { exchangeScenes } from '../../lib/exchange-copy';

test('six demo windows keep the section and every window fixed over ten seconds', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const demo = page.locator('#landing-demos').locator('..');
  const windows = demo.locator('.swb-win');
  await expect(windows).toHaveCount(6);
  await expect(demo.locator('button, [role="tablist"]')).toHaveCount(0);
  await expect(windows.first().locator('.swb-win-message:visible').first()).toBeVisible();

  const sample = () => demo.evaluate((section) => {
    const rect = (node: Element) => {
      const { x, y, width, height } = node.getBoundingClientRect();
      return { x, y, width, height };
    };
    const windows = [...section.querySelectorAll('.swb-win')];
    return {
      section: rect(section),
      windows: windows.map(rect),
      counts: windows.map((window) => window.querySelectorAll('.swb-win-message:not([data-pending])').length),
    };
  });

  const baseline = await sample();
  const counts = baseline.counts.map((count) => [count]);
  const deadline = Date.now() + 10_000;
  do {
    await page.waitForTimeout(125);
    const current = await sample();
    expect(current.section).toEqual(baseline.section);
    expect(current.windows).toEqual(baseline.windows);
    current.counts.forEach((count, i) => {
      if (counts[i].at(-1) !== count) counts[i].push(count);
    });
  } while (Date.now() < deadline);

  for (const [index, sequence] of counts.entries()) {
    const length = exchangeScenes[index].messages.length;
    expect(sequence).toContain(length);
    expect(sequence.lastIndexOf(1)).toBeGreaterThan(sequence.indexOf(length));
  }
});

test('demo grid changes from one to two to three columns at 900px and 1280px', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const grid = page.locator('.swb-exchange');
  for (const [width, columns] of [[390, 1], [899, 1], [900, 2], [1024, 2], [1279, 2], [1280, 3], [1440, 3]]) {
    await page.setViewportSize({ width, height: 900 });
    await expect.poll(() => grid.evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(columns);
  }
});

test('demo windows start at staggered times and show their own conversations', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/');
  const windows = page.locator('.swb-win');
  await expect(windows).toHaveCount(6);
  const visible = () => windows.evaluateAll((nodes) => nodes.map((node) =>
    [...node.querySelectorAll('.swb-win-message:not([data-pending])')].map((entry) => entry.querySelector('p:last-child')!.textContent)));
  await page.clock.runFor(250);
  expect(await visible()).toEqual([[exchangeScenes[0].messages[0][1].en], [], [], [], [], []]);
  await page.clock.runFor(1600);
  const active = await visible();
  expect(active.map((messages) => messages.length)).toEqual([2, 2, 2, 1, 1, 1]);
  for (const [index, messages] of active.entries()) {
    expect(messages).toEqual(exchangeScenes[index].messages.slice(0, messages.length).map(([, copy]) => copy.en));
  }
});

test('each demo keeps its last message within 24px of the bottom during playback', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const windows = page.locator('.swb-win-body');
  await expect(windows).toHaveCount(6);
  await page.clock.runFor(4400);
  for (const width of [390, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const gaps = await windows.evaluateAll((bodies) => bodies.map((body) => {
      const messages = body.querySelectorAll('.swb-win-message:not([data-pending])');
      return { gap: body.getBoundingClientRect().bottom - messages[messages.length - 1].getBoundingClientRect().bottom, count: messages.length };
    }));
    for (const { gap, count } of gaps) {
      expect(count).toBeGreaterThan(1);
      expect(gap, `${width}px bottom gap`).toBeGreaterThanOrEqual(0);
      expect(gap, `${width}px bottom gap`).toBeLessThanOrEqual(24);
    }
  }
});

test('each demo window uses canonical avatars and marks only relayed senders', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');

  const scenes = [
    { app: 'claude', title: 'Claude', senders: ['Claude', 'Grok Bot', 'Claude', 'Grok Bot'], relayed: [1, 3] },
    { app: 'muse', title: 'Muse', senders: ['You', 'Muse', 'You', 'Muse'], relayed: [] },
    { app: 'grok-bot', title: 'Grok Bot', senders: ['Grok Bot', 'Claude', 'Grok Bot', 'Capy Captain'], relayed: [1, 3] },
    { app: 'slack', title: 'Slack #launch', senders: ['You', 'Claude', 'Grok Bot', 'Capy Captain'], relayed: [1, 2, 3] },
    { app: 'github', title: 'GitHub', senders: ['You', 'Grok Bot', 'Grok Bot', 'You'], relayed: [1, 2] },
    { app: 'claude-code', title: 'Claude Code', senders: ['Capy Captain', 'Claude Code', 'Claude Code', 'Capy Captain'], relayed: [0, 3] },
  ];
  const marks: Record<string, string> = {
    Claude: '/avatars/claude.png',
    Muse: '/avatars/muse.png',
    'Grok Bot': '/avatars/grok-bot.png',
    'Capy Captain': '/marks/captain.webp',
    'Claude Code': '/avatars/claude-code.png',
  };

  for (const { app, title, senders, relayed } of scenes) {
    const window = page.locator(`[data-exchange-window="${app}"]`);
    const header = window.getByRole('heading', { level: 3 });
    await expect(header).toHaveText(title);
    const ownerMark = header.locator('.swb-win-avatar');
    if (marks[title]) await expect(ownerMark.locator('img')).toHaveAttribute('src', marks[title]);
    else await expect(ownerMark.locator('.swb-mark-glyph')).toHaveCSS('mask-image', new RegExp(`/marks/${app}\\.svg`));

    const messages = window.locator('.swb-win-message:visible');
    await expect(messages).toHaveCount(senders.length);
    for (const [index, sender] of senders.entries()) {
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

    const frames = await window.locator('.swb-win-avatar').evaluateAll((avatars) => avatars.map((avatar) => {
      const style = getComputedStyle(avatar);
      return [style.width, style.height, style.borderRadius, style.borderTopWidth, style.borderTopColor];
    }));
    expect(frames.every((frame) => JSON.stringify(frame) === JSON.stringify(frames[0]))).toBe(true);
  }
});

test('reduced motion keeps the diagram still', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);

  const board = page.locator('.swb-board:visible');
  const app = board.getByRole('button', { name: 'Claude', exact: true });
  await expect(app).toHaveCSS('animation-name', 'none');
  const canvas = board.locator('canvas');
  const spokes = board.locator('.swb-transit');
  await expect(spokes).toHaveCount(14);
  const stillSpokes = () => spokes.evaluateAll((paths) => paths.map((path) => {
    const style = getComputedStyle(path);
    return { d: path.getAttribute('d'), stroke: style.stroke, width: style.strokeWidth, active: path.hasAttribute('data-active') };
  }));
  const canvasHasPixels = () => canvas.evaluate((node: HTMLCanvasElement) => {
    const pixels = node.getContext('2d')!.getImageData(0, 0, node.width, node.height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  });
  const cords = await stillSpokes();
  expect(cords.every(({ width, stroke }) => parseFloat(width) >= 1.75 && stroke !== 'none')).toBe(true);
  expect(cords.filter(({ active }) => active)).toHaveLength(2);
  expect(await canvasHasPixels()).toBe(false);
  await page.waitForTimeout(1500);
  expect(await stillSpokes()).toEqual(cords);
  expect(await canvasHasPixels()).toBe(false);

  await app.click();
  const setup = page.getByRole('dialog', { name: 'Claude' });
  await expect(setup.getByRole('button', { name: 'Copy prompt' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(setup).toBeHidden();
  await expect(app).toBeFocused();
});

test('reduced motion shows every final transcript in a fitted row', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const demo = page.locator('.swb-exchange');
  const messages = demo.locator('.swb-win-message:visible');
  const total = exchangeScenes.reduce((sum, scene) => sum + scene.messages.length, 0);
  await expect(messages).toHaveCount(total);
  await expect(messages.first()).toHaveCSS('animation-name', 'none');
  const frame = await messages.allTextContents();
  await page.waitForTimeout(1500);
  expect(await messages.allTextContents()).toEqual(frame);

  for (const [path, lang] of [['/', 'en'], ['/zh-Hans', 'zh-Hans'], ['/zh-Hant', 'zh-Hant']] as const) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [320, 390, 899, 900, 1024, 1279, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const frames = await demo.locator('.swb-win-body').evaluateAll((bodies) => bodies.map((node) => {
        const { top, bottom, height } = node.getBoundingClientRect();
        const messages = [...node.querySelectorAll('.swb-win-message')];
        const entries = messages.map((entry) => entry.getBoundingClientRect());
        return {
          top,
          height,
          fits: entries.length === 4 && entries.every((rect) => rect.height > 0 && rect.top >= top && rect.bottom <= bottom),
          topGap: entries[0]?.top - top,
          lines: messages.map((message) => {
            const text = message.querySelector('p:last-child')!;
            return text.getBoundingClientRect().height / parseFloat(getComputedStyle(text).lineHeight);
          }),
        };
      }));
      const rowHeights = new Map<number, number>();
      for (const [index, { top, height, fits, topGap, lines }] of frames.entries()) {
        const label = `${lang} ${width}px ${exchangeScenes[index].app}`;
        expect(fits, `${label} complete transcript`).toBe(true);
        expect(topGap, `${label} top gap`).toBeLessThanOrEqual(48);
        if (width === 1440) expect(Math.max(...lines), `${label} lines per message`).toBeLessThanOrEqual(2);
        if (rowHeights.has(top)) expect(height, `${label} row height`).toBe(rowHeights.get(top));
        rowHeights.set(top, height);
      }
    }
    const accessible = await demo.ariaSnapshot();
    for (const scene of exchangeScenes) expect(accessible).toContain(scene.messages.at(-1)![1][lang]);
  }
});
