import { expect, test, type Page } from '@playwright/test';

async function headerGeometry(page: Page) {
  return page.locator('#nd-nav').evaluate((header) => {
    const rect = (item: Element) => {
      const { x, y, width, height } = item.getBoundingClientRect();
      return { x, y, width, height };
    };
    return Object.fromEntries([
      ['header', rect(header)] as const,
      ...Array.from(header.querySelectorAll<HTMLElement>('[data-header-item]'))
        .filter((item) => item.getClientRects().length)
        .map((item) => [item.dataset.headerItem!, rect(item)] as const),
    ]);
  });
}

test('header and all items stay within 0.5px across home and docs without a theme toggle', async ({ page }) => {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator('#nd-nav [data-theme-toggle]')).toHaveCount(0);
    const home = await headerGeometry(page);
    const tabs = width === 390 ? [] : ['docs', 'setup', 'about'];
    const rest = width === 390 ? ['menu'] : [];
    expect(Object.keys(home)).toEqual([
      'header', 'brand', ...tabs, width < 1024 ? 'search-icon' : 'search', 'language', 'github', ...rest,
    ]);

    for (const path of ['/docs', '/docs/install']) {
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      await expect(page.locator('#nd-nav [data-theme-toggle]')).toHaveCount(0);
      const docs = await headerGeometry(page);
      expect(Object.keys(docs)).toEqual(Object.keys(home));
      for (const item of Object.keys(home)) {
        for (const edge of ['x', 'y', 'width', 'height'] as const) {
          expect(Math.abs(docs[item][edge] - home[item][edge]), `${width}px ${path} ${item} ${edge}`).toBeLessThanOrEqual(0.5);
        }
      }
    }
  }
});

test('Ask AI clears the last paragraph, footer and cards on short and long docs pages', async ({ page }) => {
  for (const path of ['/docs', '/docs/install']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight - window.scrollY))
      .toBeLessThanOrEqual(1);
    const ask = page.getByRole('button', { name: 'Ask AI' });
    const footer = page.locator('[data-docs-footer]');
    const lastParagraph = page.locator('.swb-mdx > p').last();
    await expect(ask).toBeVisible();
    await expect(lastParagraph).toBeVisible();
    await expect(footer.locator('a').first()).toBeInViewport();
    const button = (await ask.boundingBox())!;
    const reserved = await page.locator('.swb-docs-with-chat').evaluate((node) => parseFloat(getComputedStyle(node).paddingBlockEnd));
    const bottomOffset = page.viewportSize()!.height - (button.y + button.height);
    expect(reserved).toBeGreaterThanOrEqual(button.height + bottomOffset);
    const content = [footer, lastParagraph, ...await footer.locator('a').all()];
    for (const item of content) {
      const rect = (await item.boundingBox())!;
      const intersects = button.x < rect.x + rect.width && button.x + button.width > rect.x
        && button.y < rect.y + rect.height && button.y + button.height > rect.y;
      expect(intersects, `${path} ${await item.innerText()}`).toBe(false);
    }
  }
});

test('the open-source statement is visible in all landing heroes', async ({ page }) => {
  for (const [path, text] of [
    ['/', 'Free and open source under Apache-2.0'],
    ['/zh-Hans', '免费开源，采用 Apache-2.0 许可'],
    ['/zh-Hant', '免費開源，採用 Apache-2.0 授權'],
  ]) {
    await page.goto(path);
    const license = page.locator('.swb-hero-copy').getByRole('link', { name: text });
    await expect(license).toBeInViewport();
    await expect(license).toHaveAttribute('href', 'https://github.com/ZhehanZhang/SwB/blob/release/1.0/LICENSE');
  }
});

test('landing header, hero, sections and footer share both grid edges', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);

  for (const width of [390, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const edges = await page.evaluate(() => {
      const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      const headerItems = [...document.querySelectorAll('#nd-nav [data-header-item]')]
        .filter((item) => item.getClientRects().length);
      const sections = [...document.querySelectorAll('main [data-landing-section]')];
      return {
        headerLeft: rect('#nd-nav [data-header-item="brand"]').left,
        headerRight: headerItems.at(-1)!.getBoundingClientRect().right,
        left: [rect('main h1').left, ...sections.map((section) => section.querySelector('h2')!.getBoundingClientRect().left), rect('footer a').left],
        right: [rect('.swb-hero-grid').right, ...sections.map((section) => section.getBoundingClientRect().right)],
      };
    });
    expect(edges.left).toHaveLength(5);
    for (const left of edges.left) expect(Math.abs(left - edges.headerLeft), `${width}px left edge`).toBeLessThanOrEqual(1);
    for (const right of edges.right) expect(Math.abs(right - edges.headerRight), `${width}px right edge`).toBeLessThanOrEqual(1);
  }
});

test('hero actions share a baseline, height and left edge with the command', async ({ page }) => {
  for (const [path, label] of [
    ['/', 'Set up with agent'], ['/zh-Hans', '用智能体设置'], ['/zh-Hant', '用代理設定'],
  ]) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const actions = page.locator('[data-hero-actions]');
      await expect(actions.getByRole('button', { name: label, exact: true })).toBeVisible();
      const { setup, github, chip, license, copy, hub, sameWeight, fits } = await page.evaluate(() => {
        const select = (selector: string) => document.querySelector<HTMLElement>(selector)!;
        const box = (selector: string) => select(selector).getBoundingClientRect().toJSON();
        const setupButton = select('[data-hero-actions] > button');
        const gitHubLink = select('[data-hero-actions] > a');
        return {
          setup: box('[data-hero-actions] > button'),
          github: box('[data-hero-actions] > a'),
          chip: box('[data-install-chip]'),
          license: box('.swb-hero-copy a[href$="/LICENSE"]'),
          copy: box('.swb-hero-copy'),
          hub: box('.swb-hero'),
          sameWeight: getComputedStyle(setupButton).fontWeight === getComputedStyle(gitHubLink).fontWeight,
          fits: [setupButton, gitHubLink].every((button) => button.scrollWidth <= button.clientWidth),
        };
      });
      expect(setup.height).toBe(40);
      expect(github.height).toBe(40);
      expect(Math.abs(setup.top - github.top), `${path} at ${width}px`).toBeLessThanOrEqual(0.5);
      expect(sameWeight).toBe(true);
      expect(fits).toBe(true);
      expect(github.right).toBeLessThanOrEqual(copy.right + 1);
      expect(chip.height).toBeLessThanOrEqual(28);
      if (chip.top >= github.bottom) {
        expect(width).not.toBe(1440);
        expect(Math.abs(setup.left - chip.left)).toBeLessThanOrEqual(1);
        expect(chip.top - github.bottom).toBeLessThanOrEqual(8);
      } else {
        expect(width).not.toBe(390);
        expect(chip.left).toBeGreaterThan(github.right);
        expect(Math.abs((chip.top + chip.bottom) / 2 - (github.top + github.bottom) / 2)).toBeLessThanOrEqual(1);
      }
      expect(Math.abs(setup.left - license.left)).toBeLessThanOrEqual(1);
      expect(license.top).toBeGreaterThan(Math.max(chip.bottom, github.bottom));
      if (width >= 1024) expect(Math.abs((copy.top + copy.bottom) / 2 - (hub.top + hub.bottom) / 2)).toBeLessThanOrEqual(1);
    }
  }
});

test('the inline install command copies without moving the hero controls', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const chip = page.locator('[data-install-chip]');
  const copy = chip.getByRole('button', { name: 'Copy', exact: true });
  await expect(chip.locator('code')).toHaveText('$ uvx swb-one up');
  await expect(copy.locator('svg.lucide-copy')).toHaveCount(1);
  const before = await chip.boundingBox();
  await copy.click();
  await expect(copy.locator('svg.lucide-check')).toHaveCount(1);
  await expect(chip.getByRole('status')).toHaveText('Copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('uvx swb-one up');
  expect(await chip.boundingBox()).toEqual(before);
});

test('every shared action has a leading 16px icon', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const path of ['/', '/docs', '/docs/install', '/docs/self-hosting', '/docs/setup', '/docs/setup/claude']) {
    await page.goto(path);
    const actions = page.locator('.swb-button:visible');
    const assertIcons = async () => {
      expect(await actions.count()).toBeGreaterThan(0);
      for (const action of await actions.all()) {
        const icon = action.locator(':scope > svg').first();
        await expect(icon, await action.innerText()).toHaveCSS('width', '16px');
        await expect(icon).toHaveCSS('height', '16px');
        await expect(action).toHaveCSS('column-gap', '8px');
        expect(await action.evaluate((node) => {
          const glyph = node.querySelector('svg')!;
          const label = [...node.childNodes].find((child) => child.nodeType === Node.TEXT_NODE && child.textContent?.trim());
          if (!label) return true;
          const range = document.createRange();
          range.selectNodeContents(label);
          return glyph.getBoundingClientRect().right <= range.getBoundingClientRect().left;
        })).toBe(true);
      }
    };
    await assertIcons();
    if (path === '/') {
      await page.locator('[data-hero-actions]').getByRole('button').click();
      await assertIcons();
      await page.keyboard.press('Escape');
      await page.locator('.swb-board:visible').getByRole('button', { name: 'Claude', exact: true }).click();
      await assertIcons();
    }
  }
});

test('the theme toggle sits in each footer and preserves geometry and theme on reload', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  for (const [path, selector] of [['/', '[data-site-footer]'], ['/docs/install', '[data-docs-footer]']]) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const toggle = page.locator(selector).getByRole('button', { name: 'Toggle Theme' });
    await toggle.scrollIntoViewIfNeeded();
    await expect(toggle).toBeInViewport();
    const before = await toggle.boundingBox();
    const html = page.locator('html');
    const previous = await html.evaluate((node) => node.classList.contains('dark') ? 'dark' : 'light');
    const selected = previous === 'dark' ? 'light' : 'dark';
    await toggle.click();
    await expect(html).toHaveClass(new RegExp(`\\b${selected}\\b`));
    expect(await toggle.boundingBox()).toEqual(before);
    await page.reload();
    await expect(html).toHaveClass(new RegExp(`\\b${selected}\\b`));
  }
});

test('the footer and page use the saved theme before hydration', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'));
  await page.route('**/_next/static/**/*.js', (route) => route.abort());
  for (const [path, footer] of [['/', '[data-site-footer]'], ['/docs/install', '[data-docs-footer]']]) {
    await page.goto(path);
    await expect(page.locator('html')).toHaveClass(/\bdark\b/);
    await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(9, 9, 11)');
    await expect(page.locator(`${footer} [data-theme-toggle] svg:last-child`)).toHaveCSS('background-color', 'rgb(24, 24, 27)');
  }
});

test('docs GitHub option opens the release source file', async ({ page }) => {
  await page.goto('/docs/install');
  await page.getByRole('button', { name: 'Open in', exact: true }).click();
  await expect(page.getByRole('link', { name: /Open in GitHub/ })).toHaveAttribute(
    'href', 'https://github.com/ZhehanZhang/SwB/blob/release/1.0/docs/en/install.mdx',
  );
});

test('landing shows supported deployment links and six usage examples in each locale', async ({ page }) => {
  for (const [prefix, selfHosting, allowance, paid] of [
    ['', 'Self-hosting guide', "Runs within Fly's free monthly allowance", 'Paid plan'],
    ['/zh-Hans', '自托管指南', '运行费用在 Fly 每月免费额度内', '付费套餐'],
    ['/zh-Hant', '自行代管指南', '執行費用在 Fly 每月免費額度內', '付費方案'],
  ]) {
    await page.goto(prefix || '/');
    const deploy = page.locator('#landing-deploy').locator('..');
    const commands = deploy.locator('code');
    await expect(commands.first()).toHaveText('uvx swb-one up');
    await expect(commands.last()).toContainText('git clone -b release/1.0');
    await expect(commands.last()).toContainText('docker compose up');
    const providers = deploy.locator('a.swb-button');
    await expect(providers).toHaveCount(2);
    await expect(providers.last()).toHaveText(`Render ${paid}`);
    await expect(providers.last()).toHaveAttribute('href', 'https://github.com/ZhehanZhang/SwB/blob/release/1.0/deploy/README.md#render');
    await expect(deploy.getByRole('link', { name: 'Railway', exact: true })).toHaveCount(0);
    const fly = providers.first();
    await expect(fly).toHaveText(`Fly.io ${allowance}`);
    await expect(fly).toHaveAttribute('href', `${prefix}/docs/self-hosting#fly`);
    await expect(fly).toHaveClass((await providers.last().getAttribute('class'))!);
    const usage = page.locator('#landing-usage').locator('..');
    await expect(usage.locator('p')).toHaveCount(6);
    await expect(deploy.getByRole('link', { name: selfHosting })).toHaveAttribute('href', `${prefix}/docs/self-hosting`);
    await fly.click();
    await expect(page).toHaveURL(`${prefix}/docs/self-hosting#fly`);
    await expect(page.locator('h2#docker-compose')).toContainText('Docker Compose');
    await expect(page.locator('h2#fly')).toContainText('Fly.io');
    await expect(page.locator('h2#railway')).toContainText('Railway');
    await expect(page.getByText('fly deploy --ha=false', { exact: true })).toBeVisible();
    await expect(page.locator('a[href="https://fly.io/docs/about/pricing/"]')).toBeVisible();
  }
});

test('deploy blocks stack below 900px, align at the top and hug their content', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const grid = page.locator('[data-deploy-grid]');
  for (const [width, columns] of [[390, 1], [899, 1], [900, 3], [1024, 3], [1440, 3]]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await grid.evaluate((node) => {
      const blocks = [...node.children];
      const boxes = blocks.map((block) => block.getBoundingClientRect());
      return {
        columns: getComputedStyle(node).gridTemplateColumns.split(' '),
        tops: boxes.map(({ top }) => top),
        bodyTops: blocks.map((block) => block.lastElementChild!.getBoundingClientRect().top),
        bodyHeights: blocks.map((block) => block.lastElementChild!.getBoundingClientRect().height),
        bodyOverflow: blocks.map((block, index) => block.lastElementChild!.getBoundingClientRect().right - boxes[index].right),
      };
    });
    expect(layout.columns).toHaveLength(columns);
    expect(layout.tops).toHaveLength(3);
    if (columns === 3) {
      expect(Math.max(...layout.tops) - Math.min(...layout.tops), `${width}px column tops`).toBeLessThanOrEqual(1);
      expect(Math.max(...layout.bodyTops) - Math.min(...layout.bodyTops), `${width}px body tops`).toBeLessThanOrEqual(1);
    }
    expect(layout.bodyHeights[0]).toBeLessThan(64);
    expect(layout.bodyHeights[0]).toBeLessThan(layout.bodyHeights[1]);
    for (const overflow of layout.bodyOverflow) expect(overflow, `${width}px block overflow`).toBeLessThanOrEqual(1);
  }
});
