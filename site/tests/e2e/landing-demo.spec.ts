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

test('each demo window uses canonical avatars and marks only relayed senders', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');

  const scenes = [
    { app: 'claude', title: 'Claude', senders: ['Claude', 'Grok Bot', 'Claude'], relayed: [1] },
    { app: 'muse', title: 'Muse', senders: ['You', 'Muse', 'Muse'], relayed: [] },
    { app: 'grok-bot', title: 'Grok Bot', senders: ['Grok Bot', 'Grok Bot', 'Claude', 'Capy Captain', 'Grok Bot'], relayed: [2, 3] },
    { app: 'slack', title: 'Slack #launch', senders: ['You', 'Claude', 'Grok Bot', 'Capy Captain'], relayed: [1, 2, 3] },
    { app: 'github', title: 'GitHub', senders: ['You', 'Grok Bot', 'Grok Bot'], relayed: [1, 2] },
    { app: 'claude-code', title: 'Claude Code', senders: ['Capy Captain', 'Claude Code', 'Claude Code'], relayed: [0] },
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

test('reduced motion keeps the diagram still and shows every final transcript', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);

  const board = page.locator('.swb-board:visible');
  const app = board.getByRole('button', { name: 'Claude', exact: true });
  await expect(app).toHaveCSS('animation-name', 'none');
  const canvas = board.locator('canvas');
  const cordImage = () => canvas.evaluate((node: HTMLCanvasElement) => node.toDataURL());
  await expect.poll(() => canvas.evaluate((node: HTMLCanvasElement) => {
    const pixels = node.getContext('2d')!.getImageData(0, 0, node.width, node.height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  })).toBe(true);
  const cords = await cordImage();

  const demo = page.locator('.swb-exchange');
  const messages = demo.locator('.swb-win-message:visible');
  const total = exchangeScenes.reduce((sum, scene) => sum + scene.messages.length, 0);
  await expect(messages).toHaveCount(total);
  await expect(messages.first()).toHaveCSS('animation-name', 'none');
  const frame = await messages.allTextContents();
  await page.waitForTimeout(1500);
  expect(await messages.allTextContents()).toEqual(frame);
  expect(await cordImage()).toBe(cords);

  for (const [path, lang] of [['/', 'en'], ['/zh-Hans', 'zh-Hans'], ['/zh-Hant', 'zh-Hant']] as const) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 900, 1024, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await demo.locator('.swb-win-body').evaluateAll((bodies) => bodies.every((node) => {
        const { top, bottom } = node.getBoundingClientRect();
        return [...node.querySelectorAll('.swb-win-message')].every((entry) => {
          const rect = entry.getBoundingClientRect();
          return rect.height > 0 && rect.top >= top && rect.bottom <= bottom;
        });
      })), `${lang} ${width}px complete transcripts`).toBe(true);
    }
    const accessible = await demo.ariaSnapshot();
    for (const scene of exchangeScenes) expect(accessible).toContain(scene.messages.at(-1)![1][lang]);
  }

  await page.goto('/');
  await app.click();
  const setup = page.getByRole('dialog', { name: 'Claude' });
  await expect(setup.getByRole('button', { name: 'Copy prompt' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(setup).toBeHidden();
  await expect(app).toBeFocused();
});
