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

test('hero actions share a baseline and keep mobile actions, license and command in order', async ({ page }) => {
  for (const [path, label] of [
    ['/', 'Set up with agent'], ['/zh-Hans', '用智能体设置'], ['/zh-Hant', '用代理設定'],
  ]) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const actions = page.locator('[data-hero-actions]');
      await expect(actions.getByRole('link', { name: label, exact: true })).toBeVisible();
      const { setup, github, chip, license, copy, hub, sameWeight, fits, inDomOrder } = await page.evaluate(() => {
        const select = (selector: string) => document.querySelector<HTMLElement>(selector)!;
        const box = (selector: string) => select(selector).getBoundingClientRect().toJSON();
        const setupButton = select('[data-hero-actions] > a:first-child');
        const gitHubLink = select('[data-hero-actions] > a:last-child');
        const license = select('.swb-hero-copy a[href$="/LICENSE"]').parentElement!;
        const follows = (first: Element, second: Element) => Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);
        return {
          setup: setupButton.getBoundingClientRect().toJSON(),
          github: gitHubLink.getBoundingClientRect().toJSON(),
          chip: box('[data-install-chip]'),
          license: license.getBoundingClientRect().toJSON(),
          copy: box('.swb-hero-copy'),
          hub: box('.swb-hero'),
          sameWeight: getComputedStyle(setupButton).fontWeight === getComputedStyle(gitHubLink).fontWeight,
          fits: [setupButton, gitHubLink].every((button) => button.scrollWidth <= button.clientWidth),
          inDomOrder: follows(gitHubLink, license) && follows(license, select('[data-install-chip]')),
        };
      });
      expect(setup.height).toBe(40);
      expect(github.height).toBe(40);
      expect(Math.abs(setup.top - github.top), `${path} at ${width}px`).toBeLessThanOrEqual(0.5);
      expect(sameWeight).toBe(true);
      expect(fits).toBe(true);
      expect(github.right).toBeLessThanOrEqual(copy.right + 1);
      expect(chip.height).toBeLessThanOrEqual(28);
      if (width === 390) {
        expect(inDomOrder).toBe(true);
        expect(license.top).toBeGreaterThan(github.bottom);
        expect(chip.top).toBeGreaterThan(license.bottom);
        expect(Math.abs(setup.left - chip.left)).toBeLessThanOrEqual(1);
        expect(license.top - github.bottom).toBeCloseTo(chip.top - license.bottom, 0);
      } else if (chip.top >= github.bottom) {
        expect(width).not.toBe(1440);
        expect(Math.abs(setup.left - chip.left)).toBeLessThanOrEqual(1);
        expect(chip.top - github.bottom).toBeLessThanOrEqual(8);
      } else {
        expect(chip.left).toBeGreaterThan(github.right);
        expect(Math.abs((chip.top + chip.bottom) / 2 - (github.top + github.bottom) / 2)).toBeLessThanOrEqual(1);
      }
      expect(Math.abs(setup.left - license.left)).toBeLessThanOrEqual(1);
      if (width >= 1024) expect(license.top).toBeGreaterThan(Math.max(chip.bottom, github.bottom));
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
    'href', 'https://github.com/ZhehanZhang/SwB/blob/release/1.0/site/content/docs/en/install.mdx',
  );
});

test('landing shows supported deployment links and six usage examples in each locale', async ({ page }) => {
  for (const [prefix, selfHosting, allowance, paid, hobby] of [
    ['', 'Self-hosting guide', 'Free with monthly allowance', 'Paid plan', 'Hobby plan'],
    ['/zh-Hans', '自托管指南', '月度额度内免费', '付费套餐', 'Hobby 方案'],
    ['/zh-Hant', '自行代管指南', '每月免費額度內免費', '付費方案', 'Hobby 方案'],
  ]) {
    await page.goto(prefix || '/');
    const deploy = page.locator('#landing-deploy').locator('..');
    const commands = deploy.locator('code');
    await expect(commands.first()).toHaveText('uvx swb-one up');
    await expect(commands.nth(1)).toHaveText('pipx install swb-one\nswb up');
    await expect(commands.last()).toContainText('git clone -b release/1.0');
    expect(await commands.last().textContent()).toContain('\\\n');
    await expect(commands.last()).toContainText('docker compose up');
    const providers = deploy.locator('[data-deploy-providers] a');
    await expect(providers).toHaveCount(3);
    const [fly, render, railway] = await providers.all();
    await expect(render).toHaveText(`Render ${paid}`);
    await expect(render).toHaveAttribute('href', 'https://github.com/ZhehanZhang/SwB/blob/release/1.0/deploy/README.md#render');
    await expect(railway).toHaveText(`Railway ${hobby}`);
    await expect(railway).toHaveAttribute('href', 'https://railway.com/deploy/swb');
    await expect(fly).toHaveText(`Fly.io ${allowance}`);
    await expect(fly).toHaveAttribute('href', `${prefix}/docs/self-hosting#fly`);
    await expect(fly).toHaveClass((await render.getAttribute('class'))!);
    const tunnel = deploy.locator('[data-deploy-tunnel]');
    await expect(tunnel).toHaveAttribute('href', `${prefix}/docs/self-hosting#cloudflare-tunnel`);
    await expect(tunnel).toHaveClass((await render.getAttribute('class'))!);
    await expect(deploy.locator(`a[href="${prefix}/docs/install"] > .lucide-book-open`)).toHaveCount(1);
    await expect(deploy.locator(`a[href="${prefix}/docs/self-hosting#docker-compose"] > .lucide-book-open`)).toHaveCount(1);
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

test('deploy rows show the official service marks in light and dark', async ({ page }) => {
  for (const theme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto('/');
    await expect(page.locator('html')).toHaveClass(new RegExp(`\\b${theme}\\b`));
    const rows = page.locator('[data-deploy-tunnel], [data-deploy-providers] a');
    const marks = ['/marks/cloudflare.svg', ...['fly', 'render', 'railway'].map((name) => `/marks/${name}-on-${theme}.svg`)];
    await expect(rows).toHaveCount(marks.length);
    for (const [i, mark] of marks.entries()) {
      const icon = rows.nth(i).locator('svg > image:visible');
      await expect(icon).toHaveCount(1);
      await expect(icon).toHaveAttribute('href', mark);
      const asset = await page.request.get(mark);
      expect(asset.ok()).toBe(true);
      expect(await asset.text()).toMatch(/^<svg\b/);
    }
  }
});

test('deploy columns stack below 900px, align at the top and hug their content', async ({ page }) => {
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
        heights: boxes.map(({ height }) => height),
        bodyTops: blocks.map((block) => block.children[2].getBoundingClientRect().top),
        codeBlocks: [...node.querySelectorAll('pre')].map((code) => {
          const style = getComputedStyle(code);
          const chrome = [style.paddingBlockStart, style.paddingBlockEnd, style.borderBlockStartWidth, style.borderBlockEndWidth]
            .reduce((sum, value) => sum + parseFloat(value), 0);
          return { measured: code.getBoundingClientRect().height, natural: chrome + parseFloat(style.lineHeight) * code.textContent!.split('\n').length };
        }),
      };
    });
    expect(layout.columns).toHaveLength(columns);
    expect(layout.tops).toHaveLength(3);
    if (columns === 3) {
      expect(Math.max(...layout.tops) - Math.min(...layout.tops), `${width}px column tops`).toBeLessThanOrEqual(1);
      expect(Math.max(...layout.bodyTops) - Math.min(...layout.bodyTops), `${width}px body tops`).toBeLessThanOrEqual(1);
      if (width >= 1024) expect(Math.max(...layout.heights) - Math.min(...layout.heights), `${width}px column heights`).toBeLessThanOrEqual(24);
    }
    expect(layout.codeBlocks).toHaveLength(3);
    for (const { measured, natural } of layout.codeBlocks) expect(measured).toBeCloseTo(natural, 0);
  }
});

test('provider and tunnel rows fit a single line in all three locales', async ({ page }) => {
  for (const path of ['/', '/zh-Hans', '/zh-Hant']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const rows = await page.locator('[data-deploy-providers] a, [data-deploy-tunnel]').evaluateAll((links) => links.map((link) => {
        const { width, height, right } = link.getBoundingClientRect();
        const style = getComputedStyle(link);
        const singleLine = [style.lineHeight, style.paddingBlockStart, style.paddingBlockEnd, style.borderBlockStartWidth, style.borderBlockEndWidth]
          .reduce((sum, value) => sum + parseFloat(value), 0);
        return {
          width, height, singleLine,
          contained: link.lastElementChild!.getBoundingClientRect().right <= right - parseFloat(style.paddingInlineEnd) + 1,
          whiteSpace: style.whiteSpace,
          isTunnel: link.hasAttribute('data-deploy-tunnel'),
        };
      }));
      expect(rows).toHaveLength(4);
      const cloud = rows.filter((row) => !row.isTunnel);
      expect(new Set(cloud.map(({ width }) => width)).size).toBe(1);
      expect(new Set(rows.map(({ height }) => height)).size).toBe(1);
      for (const row of rows) {
        expect(row.whiteSpace).toBe('nowrap');
        expect(row.contained).toBe(true);
        expect(Math.abs(row.height - row.singleLine), `${path} provider height at ${width}px`).toBeLessThanOrEqual(2);
      }
    }
  }
});
