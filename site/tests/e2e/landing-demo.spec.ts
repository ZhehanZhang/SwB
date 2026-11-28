import { expect, test } from '@playwright/test';
import { observeLayoutShifts, readLayoutShifts } from './layout-shifts';
import { exchangeScenes } from '../../lib/exchange-copy';

function firstMessageGaps(windows: Element[]) {
  return windows.map((window) => window.querySelector('.swb-win-live .swb-win-message')!.getBoundingClientRect().top -
    window.querySelector('.swb-win-bar')!.getBoundingClientRect().bottom);
}

test.describe('server-rendered demo', () => {
  test.use({ javaScriptEnabled: false, reducedMotion: 'no-preference' });

  test('starts each transcript 10px below the header', async ({ page }) => {
    await page.goto('/');
    const windows = page.locator('.swb-win');
    await expect(windows).toHaveCount(exchangeScenes.length);
    await expect(windows.first().locator('.swb-win-live .swb-win-message')).toBeVisible();
    expect(await windows.evaluateAll(firstMessageGaps)).toEqual(Array(exchangeScenes.length).fill(10));
  });
});

test('six demo windows keep the section and every window fixed over ten seconds', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.addInitScript(observeLayoutShifts);
  await page.route('**/_next/static/media/jetbrains_mono*.woff2', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 650));
    await route.continue();
  });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const demo = page.locator('#landing-demos').locator('..');
  const windows = demo.locator('.swb-win');
  await expect(windows).toHaveCount(6);
  await expect(demo.locator('button, [role="tablist"]')).toHaveCount(0);
  await expect(windows.first().locator('.swb-win-live .swb-win-message').first()).toBeVisible();

  const sample = () => demo.evaluate((section) => {
    const rect = (node: Element) => {
      const { x, y, width, height } = node.getBoundingClientRect();
      return { x, y, width, height };
    };
    const windows = [...section.querySelectorAll('.swb-win')];
    return {
      section: rect(section),
      windows: windows.map(rect),
      titleBars: windows.map((window) => rect(window.querySelector('.swb-win-bar')!)),
    };
  });

  const baseline = await sample();
  expect(baseline.windows.every(({ height }) => height === 244)).toBe(true);
  expect(baseline.titleBars.every(({ height }) => height === 44)).toBe(true);
  const deadline = Date.now() + 10_000;
  do {
    await page.waitForTimeout(125);
    const current = await sample();
    expect(current).toEqual(baseline);
  } while (Date.now() < deadline);
  const shifts = await page.evaluate(readLayoutShifts);
  expect(shifts.reduce((total, shift) => total + shift.value, 0), JSON.stringify(shifts, null, 2)).toBe(0);
});

test('Simplified Chinese dark landing at 1024px has zero page-wide CLS at load and during playback', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 1000 });
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'no-preference' });
  await page.addInitScript(observeLayoutShifts);
  await page.route('**/_next/static/media/jetbrains_mono*.woff2', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 650));
    await route.continue();
  });
  await page.goto('/zh-Hans');
  await expect(page.locator('html')).toHaveClass(/\bdark\b/);
  const demo = page.locator('.swb-exchange');
  const windows = demo.locator('.swb-win');
  await expect(windows).toHaveCount(exchangeScenes.length);
  expect(await windows.evaluateAll(firstMessageGaps)).toEqual(Array(exchangeScenes.length).fill(10));
  await page.evaluate(() => document.fonts.ready);

  const expectZeroCLS = async () => {
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const shifts = await page.evaluate(readLayoutShifts);
    expect(shifts.reduce((total, shift) => total + shift.value, 0), JSON.stringify(shifts, null, 2)).toBe(0);
  };
  await expectZeroCLS();

  await demo.evaluate((node) => node.scrollIntoView());
  const transcript = windows.first().locator('.swb-win-live');
  const messages = transcript.locator('.swb-win-message');
  await expect(messages).toHaveCount(5, { timeout: 12_000 });
  await expect(messages.last().locator('p:last-child')).toHaveText(exchangeScenes[0].messages[0][1]['zh-Hans']);
  await expect.poll(() => transcript.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
  await expect(messages).toHaveCount(8, { timeout: 12_000 });
  await expect(messages.first().locator('p:last-child')).toHaveText(exchangeScenes[0].messages[1][1]['zh-Hans']);
  await expectZeroCLS();
});

test('demo transcripts never disappear across two loops at 50ms intervals', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/');
  await expect(page.locator('.swb-win-live')).toHaveCount(6);

  const frames = await page.locator('.swb-exchange').evaluate((demo) => new Promise<{
    time: number;
    windows: { opacity: number; visible: number; latest: string | null }[];
  }[]>((resolve) => {
    const samples: {
      time: number;
      windows: { opacity: number; visible: number; latest: string | null }[];
    }[] = [];
    const bodies = [...demo.querySelectorAll('.swb-win-live')];
    const start = performance.now();
    const sample = () => {
      samples.push({
        time: performance.now() - start,
        windows: bodies.map((body) => {
          const { top, bottom } = body.getBoundingClientRect();
          const entries = [...body.querySelectorAll('.swb-win-message')];
          return {
            opacity: Number(getComputedStyle(body).opacity),
            visible: entries.filter((entry) => {
              const style = getComputedStyle(entry);
              const rect = entry.getBoundingClientRect();
              const opacity = Number(style.opacity);
              return style.visibility === 'visible' && opacity > 0 &&
                rect.height > 0 && rect.bottom > top && rect.top < bottom;
            }).length,
            latest: entries.at(-1)?.querySelector('p:last-child')?.textContent ?? null,
          };
        }),
      });
    };
    sample();
    const timer = window.setInterval(() => {
      sample();
      if (samples.at(-1)!.time < 18_500) return;
      window.clearInterval(timer);
      resolve(samples);
    }, 50);
  }));

  const periods = [];
  for (const [i, scene] of exchangeScenes.entries()) {
    const copies = scene.messages.map(([, text]) => text.en);
    const wrapTimes = [];
    let previous: string | null = null;
    let changes = 0;
    for (const { time, windows } of frames) {
      const { opacity, visible, latest } = windows[i];
      expect(opacity, `${scene.app} opacity at ${time}ms`).toBeGreaterThan(0);
      expect(visible, `${scene.app} visible messages at ${time}ms`).toBeGreaterThan(0);
      if (latest === previous) continue;
      if (previous !== null) {
        expect(latest, `${scene.app} next message at ${time}ms`).toBe(copies[(copies.indexOf(previous) + 1) % copies.length]);
        if (latest === copies[0]) wrapTimes.push(time);
        changes++;
      }
      previous = latest;
    }
    expect(changes, `${scene.app} full cycles`).toBeGreaterThanOrEqual(copies.length * 2);
    expect(wrapTimes.length, `${scene.app} observed wraps`).toBeGreaterThanOrEqual(2);
    periods.push(wrapTimes[1] - wrapTimes[0]);
  }
  for (let i = 1; i < periods.length; i++) {
    expect(periods[i], `${exchangeScenes[i].app} cycle period`).toBeGreaterThan(periods[i - 1]);
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

test('demo windows advance at staggered times and show their own conversations', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/');
  const windows = page.locator('.swb-win-live');
  await expect(windows).toHaveCount(6);
  const visible = () => windows.evaluateAll((nodes) => nodes.map((node) =>
    [...node.querySelectorAll('.swb-win-message')].map((entry) => entry.querySelector('p:last-child')!.textContent)));
  await page.clock.runFor(250);
  expect(await visible()).toEqual(exchangeScenes.map(({ messages }) => [messages[0][1].en]));
  expect(await page.locator('.swb-win').evaluateAll(firstMessageGaps)).toEqual(Array(exchangeScenes.length).fill(10));
  await page.clock.runFor(1600);
  const active = await visible();
  expect(active.map((messages) => messages.length)).toEqual([2, 2, 2, 1, 1, 1]);
  for (const [index, messages] of active.entries()) {
    expect(messages).toEqual(exchangeScenes[index].messages.slice(0, messages.length).map(([, copy]) => copy.en));
  }
  expect(await page.locator('.swb-win').evaluateAll(firstMessageGaps)).toEqual(Array(exchangeScenes.length).fill(10));
});

test('new messages fade and rise 8px, and earlier messages glide up only on overflow', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/');
  const body = page.locator('.swb-win-live').first();
  await page.clock.runFor(1260);
  const messages = body.locator('.swb-win-message');
  await expect(messages).toHaveCount(2);
  const entry = await messages.last().evaluate((message) => {
    const track = message.getAnimations()[0].effect as KeyframeEffect;
    return { start: track.getKeyframes()[0], duration: track.getTiming().duration };
  });
  expect(entry).toMatchObject({ duration: 250, start: { opacity: '0', transform: 'translateY(8px)', easing: 'ease-out' } });

  await page.clock.runFor(2750);
  await expect(messages).toHaveCount(4);
  expect(await body.evaluate((node) => node.scrollTop)).toBe(0);
  const positions = body.evaluate((node) => new Promise<number[]>((resolve) => {
    const samples: number[] = [];
    node.addEventListener('scroll', () => samples.push(node.scrollTop));
    node.addEventListener('scrollend', () => resolve(samples), { once: true });
  }));
  await page.clock.runFor(3200);
  await expect(messages).toHaveCount(5);
  const samples = await positions;
  const bottom = samples.at(-1)!;
  expect(bottom).toBeGreaterThan(0);
  expect(samples.some((position) => position > 0 && position < bottom)).toBe(true);
});

test('each overflowing demo keeps its last message within 24px of the bottom during playback', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const windows = page.locator('.swb-win-live');
  await expect(windows).toHaveCount(6);
  await page.clock.runFor(10_500);
  for (const width of [390, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect.poll(() => windows.evaluateAll((bodies) => bodies.map((body) => {
      const messages = body.querySelectorAll('.swb-win-message');
      const gap = body.getBoundingClientRect().bottom - messages[messages.length - 1].getBoundingClientRect().bottom;
      return body.scrollTop > 0 && gap >= 0 && gap <= 24;
    })), `${width}px overflowing transcripts and bottom gaps`).toEqual(Array(exchangeScenes.length).fill(true));
  }
});

test('every demo title links to setup for its apps in each locale', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const agents = [
    'claude,grok-bot',
    'muse',
    'grok-bot,claude,capy',
    'slack,claude,grok-bot,capy',
    'github,grok-bot',
    'claude-code,capy',
  ];
  for (const [prefix, label] of [['', 'Set this up'], ['/zh-Hans', '设置这个场景'], ['/zh-Hant', '設定這個情境']]) {
    await page.goto(`${prefix}/`);
    for (const [i, { app }] of exchangeScenes.entries()) {
      const window = page.locator(`[data-exchange-window="${app}"]`);
      const link = window.locator('.swb-win-bar').getByRole('link', { name: label });
      await expect(link).toBeVisible();
      const href = new URL((await link.getAttribute('href'))!, page.url());
      expect(href.pathname).toBe(`${prefix}/docs/get-started`);
      expect(href.searchParams.get('agents')).toBe(agents[i]);
      await expect(link.locator('svg')).toHaveCSS('width', '14px');
    }
  }
});

test('each demo title and setup label stays on one line at every target width', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const path of ['/', '/zh-Hans', '/zh-Hant']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const bars = await page.locator('.swb-win-bar').evaluateAll((nodes) => nodes.map((bar) => {
        const line = (element: Element) => {
          const range = document.createRange();
          range.selectNodeContents(element);
          return Array.from(range.getClientRects(), ({ top, right }) => ({ top, right }));
        };
        const title = bar.querySelector('h3 > span:last-child')!;
        const link = bar.querySelector('a')!;
        const label = link.querySelector('span')!;
        return {
          title: title.textContent,
          titleLines: line(title),
          labelLines: line(label),
          titleRight: title.getBoundingClientRect().right,
          linkLeft: link.getBoundingClientRect().left,
          linkRight: link.getBoundingClientRect().right,
          barRight: bar.getBoundingClientRect().right,
        };
      }));
      expect(bars).toHaveLength(exchangeScenes.length);
      for (const bar of bars) {
        const at = `${path} at ${width}px: ${bar.title}`;
        expect(bar.titleLines, at).toHaveLength(1);
        expect(bar.labelLines, at).toHaveLength(1);
        expect(Math.abs(bar.titleLines[0].top - bar.labelLines[0].top), at).toBeLessThanOrEqual(4);
        expect(bar.titleLines[0].right, at).toBeLessThanOrEqual(bar.titleRight + 1);
        expect(bar.titleRight, at).toBeLessThan(bar.linkLeft);
        expect(bar.labelLines[0].right, at).toBeLessThanOrEqual(bar.linkRight + 1);
        expect(bar.linkRight, at).toBeLessThan(bar.barRight);
      }
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
  const app = board.getByRole('link', { name: 'Claude', exact: true });
  await expect(app).toHaveCSS('animation-name', 'none');
  const pulse = board.locator('.swb-hub-pulse:visible');
  const spokes = board.locator('.swb-transit');
  await expect(spokes).toHaveCount(14);
  const stillSpokes = () => spokes.evaluateAll((paths) => paths.map((path) => {
    const style = getComputedStyle(path);
    return { d: path.getAttribute('d'), fill: style.fill, stroke: style.stroke, active: path.hasAttribute('data-active') };
  }));
  const cords = await stillSpokes();
  expect(cords.every(({ d, fill, stroke }) => d?.includes(' Q ') && fill === 'none' && stroke !== 'none')).toBe(true);
  expect(cords.filter(({ active }) => active)).toHaveLength(2);
  await expect(pulse).toHaveCount(0);
  await page.waitForTimeout(1500);
  expect(await stillSpokes()).toEqual(cords);
  await expect(pulse).toHaveCount(0);

  await app.focus();
  await expect(app).toBeFocused();
  await expect(app).toHaveAttribute('href', '/docs/get-started?agents=claude');
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
      const frames = await demo.locator('.swb-win-final').evaluateAll((bodies) => bodies.map((node) => {
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
        expect(topGap, `${label} top gap`).toBe(10);
        if (width === 1440) expect(Math.max(...lines), `${label} lines per message`).toBeLessThanOrEqual(2);
        if (rowHeights.has(top)) expect(height, `${label} row height`).toBe(rowHeights.get(top));
        rowHeights.set(top, height);
      }
    }
    const accessible = await demo.ariaSnapshot();
    for (const scene of exchangeScenes) expect(accessible).toContain(scene.messages.at(-1)![1][lang]);
  }
});
