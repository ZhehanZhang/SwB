import { expect, test, type Locator } from '@playwright/test';
import { localize, type Locale } from '../../lib/i18n';

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
  await expect(deploy.getByRole('link', { name: 'Compose' })).toHaveAttribute('href', '/docs/deploy#docker-compose');
  await expect(deploy.locator('[data-deploy-tunnel] [data-provider-arrow]')).toBeVisible();
});

test('the landing Deploy guide row links to the current page in every locale', async ({ page }) => {
  const labels = [
    ['en', 'Deploy guide'], ['zh-Hans', '部署指南'], ['zh-Hant', '部署指南'], ['es', 'Guía de despliegue'],
    ['fr', 'Guide de déploiement'], ['de', 'Anleitung zur Bereitstellung'], ['ko', '배포 가이드'], ['ja', 'デプロイガイド'],
  ] as const satisfies ReadonlyArray<readonly [Locale, string]>;
  for (const [locale, label] of labels) {
    await page.goto(localize(locale, '/'));
    const row = page.locator('#landing-deploy + [data-deploy-grid] [data-deploy-secondary]');
    await expect(row.getByRole('link', { name: label, exact: true })).toHaveAttribute('href', localize(locale, '/docs/deploy'));
  }
});

test('landing shows uvx and pipx together with every deploy column the same height', async ({ page }) => {
  for (const locale of ['en', 'zh-Hans', 'zh-Hant', 'es', 'fr', 'de', 'ko', 'ja'] as const) {
    await page.goto(localize(locale, '/'));
    await page.evaluate(() => document.fonts.ready);
    const grid = page.locator('[data-deploy-grid]');
    await expect(grid.locator('summary, details, [role="tab"]')).toHaveCount(0);
    await expect(grid.locator('[data-deploy-local] code')).toHaveText(['uvx swb.one up', 'pipx install swb.one\nswb up']);
    await expect(grid.locator('[data-deploy-local]').getByRole('button', { name: /./ })).toHaveCount(2);
    if (page.viewportSize()!.width < 900) continue;
    const layout = await grid.evaluate((node) => ({
      options: [...node.querySelectorAll('[data-deploy-option]')].map((option) => option.getBoundingClientRect().height),
      primary: [...node.querySelectorAll('[data-deploy-primary] > *')].map((option) => option.getBoundingClientRect().height),
    }));
    expect(new Set(layout.options).size, locale).toBe(1);
    expect(new Set(layout.primary).size, locale).toBe(1);
  }
});

test('landing commands use matching copy controls and copy the exact text', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  const grid = page.locator('[data-deploy-grid]');
  const commands = grid.locator('[data-deploy-command]');
  await expect(grid.getByRole('button', { name: 'Copy', exact: true })).toHaveCount(3);
  for (const command of await commands.all()) {
    const copy = command.getByRole('button', { name: 'Copy', exact: true });
    const measure = () => command.evaluate((node) => {
      const { top, right } = node.querySelector('pre')!.getBoundingClientRect();
      const button = node.querySelector('button')!.getBoundingClientRect();
      return { top: button.top - top, right: right - button.right, width: button.width, height: button.height };
    });
    const position = { top: 4, right: 4, width: 24, height: 24 };
    expect(await measure()).toEqual(position);
    await copy.click();
    await expect(command.getByRole('status')).toHaveText('Copied');
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await command.locator('code').textContent());
    expect(await measure()).toEqual(position);
  }
});

test('Get started uses one width and aligned actions for every run location', async ({ page }) => {
  await page.goto('/docs/get-started');
  const run = page.getByRole('region', { name: 'Where SwB runs' });
  const field = run.getByRole('textbox', { name: 'Your SwB address' });
  const switcher = run.getByRole('group', { name: 'Where SwB runs' });
  for (const [kind, label, guide] of [
    ['local', 'On your computer', 'Install guide'],
    ['server', 'On your server', 'Deploy guide'],
    ['cloud', 'In the cloud', 'Deploy guide'],
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
  for (const [path, selector] of [['/', 'section[aria-labelledby="landing-deploy"]'], ['/docs/get-started?run=cloud', '[data-start-run="cloud"]'], ['/docs/deploy', '.swb-mdx']]) {
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
      return {
        paddingInline: [css.paddingInlineStart, css.paddingInlineEnd],
        font: css.fontSize, line: css.lineHeight, icon: icon.width,
      };
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
