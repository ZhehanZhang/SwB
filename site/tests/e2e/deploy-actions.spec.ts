import { expect, test, type Locator } from '@playwright/test';

test.use({ reducedMotion: 'reduce' });

async function expectGuideInHeader(header: Locator) {
  const guide = header.locator('[data-block-guides] a:visible').first();
  await expect(guide).toBeVisible();
  const result = await header.evaluate((element) => {
    function baseline(text: Element) {
      const mark = document.createElement('span');
      mark.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
      text.appendChild(mark);
      const y = mark.getBoundingClientRect().top;
      mark.remove();
      return y;
    }
    const title = element.querySelector(':scope > :is(h2,h3)')!;
    const label = [...element.querySelectorAll('[data-block-guides] a > span')]
      .find((node) => node.getBoundingClientRect().height)!;
    return {
      delta: Math.abs(baseline(title) - baseline(label)),
      gap: getComputedStyle(element).columnGap,
      right: element.getBoundingClientRect().right - label.getBoundingClientRect().right,
    };
  });
  expect(result.delta).toBeLessThanOrEqual(1);
  expect(result.right).toBeLessThanOrEqual(1);
  expect(result.gap).toBe('12px');
}

async function expectActionRow(row: Locator) {
  const geometry = await row.evaluate((element) => {
    const [first, second] = element.querySelectorAll(':scope > :is(button,a)');
    const a = first.getBoundingClientRect();
    const b = second.getBoundingClientRect();
    return { gap: getComputedStyle(element).gap, actualGap: b.left - a.right, delta: Math.abs(b.top - a.top) };
  });
  expect(geometry.gap).toBe('12px');
  expect(Math.abs(geometry.actualGap - 12)).toBeLessThanOrEqual(1);
  expect(geometry.delta).toBeLessThanOrEqual(1);
}

test('landing deploy guides align with their block titles', async ({ page }) => {
  await page.goto('/');
  const deploy = page.locator('section[aria-labelledby="landing-deploy"]');
  const headers = deploy.locator('[data-block-header]').filter({ has: page.locator('a') });
  await expect(headers).toHaveCount(2);
  for (const header of await headers.all()) await expectGuideInHeader(header);
  await expect(deploy.getByRole('link', { name: 'Compose' })).toHaveAttribute('href', '/docs/self-hosting#docker-compose');
  await expect(deploy.locator('[data-deploy-secondary]').getByRole('link', { name: 'Self-hosting guide' })).toHaveAttribute('href', '/docs/self-hosting');
  await expect(deploy.locator('[data-deploy-tunnel] [data-provider-arrow]')).toBeVisible();
});

test('landing pipx commands open below their row without moving the aligned rows', async ({ page }) => {
  await page.goto('/');
  const grid = page.locator('[data-deploy-grid]');
  const toggle = grid.locator('summary').filter({ hasText: 'Use pipx' });
  const commands = grid.locator('details pre');
  const following = grid.locator('[data-deploy-option]').nth(1).locator('[data-block-header]');
  await expect(commands).toBeHidden();
  await toggle.scrollIntoViewIfNeeded();
  const before = await following.evaluate((node) => node.getBoundingClientRect().top + scrollY);
  const row = (await toggle.boundingBox())!;
  await toggle.press('Enter');
  await expect(commands).toBeVisible();
  expect(await commands.locator('code').textContent()).toBe('pipx install swb-one\nswb up');
  const expanded = (await commands.boundingBox())!;
  expect(expanded.y - row.y - row.height).toBe(8);
  const after = await following.evaluate((node) => node.getBoundingClientRect().top + scrollY);
  expect(after).toBe(before + (page.viewportSize()!.width < 900 ? expanded.height + 8 : 0));
  await toggle.press('Space');
  await expect(commands).toBeHidden();
  expect(await following.evaluate((node) => node.getBoundingClientRect().top + scrollY)).toBe(before);
});

test('Get started uses one width and aligned actions for every run location', async ({ page }) => {
  await page.goto('/docs/get-started');
  const run = page.getByRole('region', { name: 'Where SwB runs' });
  const field = run.getByRole('textbox', { name: 'Your SwB address' });
  const switcher = run.getByRole('group', { name: 'Where SwB runs' });
  for (const [kind, label, guide] of [
    ['local', 'On your computer', 'Install guide'],
    ['server', 'On your server', 'Self-hosting guide'],
    ['cloud', 'In the cloud', 'Self-hosting guide'],
  ]) {
    await switcher.getByRole('button', { name: label }).click();
    const panel = run.locator(`[data-start-run="${kind}"]`);
    await expect(panel).toBeVisible();
    await expect(run.getByRole('link', { name: guide, exact: true })).toBeVisible();
    await expectGuideInHeader(run.locator('[data-block-header]'));
    const content = kind === 'cloud' ? panel.locator('[data-deploy-providers]') : panel.locator('[data-command-actions]').first().locator('..');
    const [tabBox, fieldBox, contentBox] = await Promise.all([switcher, field, content].map((locator) => locator.boundingBox()));
    for (const box of [fieldBox!, contentBox!]) {
      expect(Math.abs(tabBox!.x - box.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(tabBox!.width - box.width)).toBeLessThanOrEqual(1);
    }
    if (kind !== 'cloud') await expectActionRow(panel.locator('[data-command-actions]').first());
  }
});

test('provider tiles share one design and visible deploy affordances on landing and docs', async ({ page }) => {
  let chrome: unknown;
  for (const [path, selector] of [['/', 'section[aria-labelledby="landing-deploy"]'], ['/docs/get-started?run=cloud', '[data-start-run="cloud"]'], ['/docs/self-hosting', '.swb-mdx']]) {
    await page.goto(path);
    const grid = page.locator(selector).locator('[data-deploy-providers]');
    const tiles = grid.getByRole('link');
    await expect(tiles).toHaveCount(3);
    if (path === '/') {
      await expect(grid).toHaveCSS('border-radius', '6px');
      await expect(grid).toHaveCSS('border-width', '1px');
      await expect(tiles.first()).toHaveCSS('border-bottom-width', '1px');
      await expect(tiles.last()).toHaveCSS('border-width', '0px');
    } else {
      await expect(grid).toHaveCSS('gap', '12px');
    }
    const layout = await grid.evaluate((node) => {
      const columns = getComputedStyle(node).gridTemplateColumns.split(' ').filter((width) => width !== '0px');
      const boxes = [...node.querySelectorAll('a')].map((item) => item.getBoundingClientRect());
      return { columns: columns.length, widthDelta: Math.max(...boxes.map(({ width }) => width)) - Math.min(...boxes.map(({ width }) => width)) };
    });
    const desktop = page.viewportSize()!.width === 1440;
    expect(layout.columns).toBe(desktop && path !== '/' ? 3 : 1);
    expect(layout.widthDelta).toBeLessThanOrEqual(1);
    const tile = tiles.first();
    await expect(tile.locator('image:visible')).toBeVisible();
    await expect(tile.locator('[data-provider-arrow]')).toBeVisible();
    const style = await tile.evaluate((item) => {
      const css = getComputedStyle(item);
      const icon = getComputedStyle(item.querySelector('svg')!);
      return { padding: css.padding, font: css.fontSize, line: css.lineHeight, icon: icon.width };
    });
    chrome ??= style;
    expect(style).toEqual(chrome);
    const initial = await tile.evaluate((item) => getComputedStyle(item).backgroundColor);
    await tile.hover();
    await expect(tile).not.toHaveCSS('background-color', initial);
    await page.keyboard.press('Tab');
    await tile.focus();
    await expect(tile).toHaveCSS('outline-style', 'solid');
  }
});

test('prompt toolbar keeps Copy and Open buttons on the same action baseline', async ({ page }) => {
  await page.goto('/docs/setup/claude');
  const toolbar = page.locator('.swb-prompt [data-prompt-actions]').first();
  await expectActionRow(toolbar);
});
