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

test('header and all items stay within 0.5px across home and docs', async ({ page }) => {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.evaluate(() => document.fonts.ready);
    const home = await headerGeometry(page);
    const tabs = width === 390 ? [] : ['docs', 'setup', 'about'];
    const rest = width === 390 ? ['menu'] : [];
    expect(Object.keys(home)).toEqual([
      'header', 'brand', ...tabs, width < 1024 ? 'search-icon' : 'search', 'theme', 'language', 'github', ...rest,
    ]);

    for (const path of ['/docs', '/docs/install']) {
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
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
  for (const path of ['/', '/zh-Hans', '/zh-Hant']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const [setup, github, chip, license, copy, hub] = await page.evaluate(() =>
        ['[data-hero-actions] > button', '[data-hero-actions] > a', '[data-install-chip]', '.swb-hero-copy a[href$="/LICENSE"]', '.swb-hero-copy', '.swb-hero']
          .map((selector) => document.querySelector(selector)!.getBoundingClientRect().toJSON()),
      );
      expect(Math.abs(setup.height - github.height)).toBeLessThanOrEqual(1);
      expect(Math.abs(setup.height - chip.height)).toBeLessThanOrEqual(1);
      expect(Math.abs(setup.bottom - github.bottom)).toBeLessThanOrEqual(1);
      expect(Math.abs(setup.left - chip.left)).toBeLessThanOrEqual(1);
      expect(Math.abs(github.right - chip.right)).toBeLessThanOrEqual(1);
      expect(Math.abs(setup.left - license.left)).toBeLessThanOrEqual(1);
      expect(license.top).toBeGreaterThan(chip.bottom);
      if (width >= 1024) expect(Math.abs((copy.top + copy.bottom) / 2 - (hub.top + hub.bottom) / 2)).toBeLessThanOrEqual(1);
    }
  }
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
    await expect(providers.last()).toHaveAttribute('href', /render\.com\/deploy\?repo=.*release%2F1\.0/);
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
    await expect(page.getByText('fly deploy --ha=false', { exact: true })).toBeVisible();
    await expect(page.locator('a[href="https://fly.io/docs/about/pricing/"]')).toBeVisible();
  }
});

test('deploy blocks stack below 900px and have equal heights at 1024px and 1440px', async ({ page }) => {
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
        heights: boxes.map(({ height }) => height),
        bodyOverflow: blocks.map((block, index) => block.lastElementChild!.getBoundingClientRect().right - boxes[index].right),
      };
    });
    expect(layout.columns).toHaveLength(columns);
    expect(layout.heights).toHaveLength(3);
    expect(Math.max(...layout.heights) - Math.min(...layout.heights), `${width}px block heights`).toBeLessThanOrEqual(1);
    for (const overflow of layout.bodyOverflow) expect(overflow, `${width}px block overflow`).toBeLessThanOrEqual(1);
  }
});
