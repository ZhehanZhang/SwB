import { once } from 'node:events';
import { createServer, get, type ServerResponse } from 'node:http';
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

test('the shared header keeps its geometry across home and docs except for the search offset', async ({ page }) => {
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator('#nd-nav [data-theme-toggle]')).toHaveCount(0);
    const home = await headerGeometry(page);
    const tabs = width === 390 ? [] : ['docs', 'setup', 'about'];
    const rest = width === 390 ? ['menu'] : [];
    const search = width < 1024 ? 'search-icon' : 'search';
    expect(Object.keys(home)).toEqual([
      'header', 'brand', ...tabs, search, 'address', 'language', 'github', ...rest,
    ]);

    for (const path of ['/docs', '/docs/install']) {
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      await expect(page.locator('#nd-nav [data-theme-toggle]')).toHaveCount(0);
      const docs = await headerGeometry(page);
      const { version } = docs;
      expect(version.height).toBe(home.language.height);
      expect(version.width).toBe(64);
      expect(Object.keys(docs)).toEqual([
        'header', 'brand', ...tabs, search, 'version', 'address', 'language', 'github', ...rest,
      ]);
      for (const item of Object.keys(home)) {
        for (const edge of ['x', 'y', 'width', 'height'] as const) {
          const offset = item === search && edge === 'x' ? version.width + (width < 640 ? 4 : 8) : 0;
          expect(Math.abs(docs[item][edge] - home[item][edge] + offset), `${width}px ${path} ${item} ${edge}`).toBeLessThanOrEqual(0.5);
        }
      }
    }
  }
});

test('landing, About and docs render matching header control gaps before hydration', async ({ page }) => {
  await page.route('**/_next/static/**/*.js', (route) => route.abort());
  const width = page.viewportSize()!.width;
  const gap = width < 640 ? 4 : 8;
  let right: number | undefined;
  for (const path of ['/', '/about', '/docs/install']) {
    await page.goto(path);
    const cluster = page.locator('[data-header-controls]');
    await expect(cluster.locator('[data-header-item="address"]')).toBeVisible();
    const docs = path.startsWith('/docs/');
    await expect(cluster.locator('[data-header-item="version"]')).toHaveCount(docs ? 1 : 0);
    const layout = await cluster.evaluate((element) => {
      const items = [...element.querySelectorAll<HTMLElement>('[data-header-item]')]
        .filter((control) => control.getClientRects().length);
      const controls = items.map((control) => control.getBoundingClientRect());
      return {
        order: items.map((control) => control.dataset.headerItem),
        columns: getComputedStyle(element).gridTemplateColumns.split(' ').length,
        gaps: controls.slice(1).map((control, index) => control.left - controls[index].right),
        right: element.getBoundingClientRect().right,
        last: controls.at(-1)!.right,
      };
    });
    expect(layout.order, path).toEqual([
      width < 1024 ? 'search-icon' : 'search', ...(docs ? ['version'] : []), 'address', 'language', 'github', ...(width < 640 ? ['menu'] : []),
    ]);
    expect(layout.gaps).toHaveLength((gap === 4 ? 4 : 3) + Number(docs));
    expect(layout.columns).toBe(layout.gaps.length + 1);
    for (const measured of layout.gaps) expect(Math.abs(measured - gap), path).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.last - layout.right), path).toBeLessThanOrEqual(1);
    right ??= layout.right;
    expect(Math.abs(layout.right - right), path).toBeLessThanOrEqual(1);
  }
});

test('the brand slot keeps the docs navigation fixed while its font loads', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  let finishFont!: () => void;
  const fontResponse = new Promise<void>((resolve) => { finishFont = resolve; });
  await page.route('**/_next/static/media/inter_tight*.woff2', async (route) => {
    await fontResponse;
    await route.continue();
  });
  try {
    await page.goto('/docs', { waitUntil: 'commit' });
    await page.waitForFunction(() => performance.getEntriesByName('first-contentful-paint').length > 0);
    const before = await headerGeometry(page);
    finishFont();
    await page.evaluate(() => document.fonts.ready);
    expect(await headerGeometry(page)).toEqual(before);
  } finally {
    finishFont();
  }
});

test('a streamed header keeps the search control in its final position', async ({ page, request, baseURL }) => {
  const response = await request.get('/docs');
  expect(response.ok()).toBe(true);
  const html = await response.text();
  const item = page.viewportSize()!.width >= 1024 ? 'search' : 'search-icon';
  const marker = item === 'search' ? 'data-search-full=""' : 'data-search=""';
  const search = html.indexOf(marker);
  expect(search).toBeGreaterThan(0);
  const closingTag = item === 'search' ? '</button>' : '</button></div>';
  const split = html.indexOf(closingTag, search) + closingTag.length;
  expect(split).toBeGreaterThan(search);
  await page.addInitScript(() => {
    new PerformanceObserver((entries) => {
      const moved = entries.getEntries().some((entry) =>
        (entry as PerformanceEntry & { sources: { node?: Node }[] }).sources.some(({ node }) =>
          document.querySelector('#nd-nav')?.contains(node ?? null)));
      if (moved) document.documentElement.dataset.headerShift = 'true';
    }).observe({ type: 'layout-shift', buffered: true });
  });
  const upstream = new URL(baseURL!).origin;
  let documentResponse: ServerResponse | undefined;
  const server = createServer((incoming, outgoing) => {
    if (incoming.url === '/docs') {
      documentResponse = outgoing;
      outgoing.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      outgoing.write(html.slice(0, split));
      return;
    }
    get(upstream + incoming.url, (asset) => {
      outgoing.writeHead(asset.statusCode!, asset.headers);
      asset.pipe(outgoing);
    }).on('error', () => outgoing.destroy());
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  try {
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Expected a TCP address');
    await page.goto(`http://127.0.0.1:${address.port}/docs`, { waitUntil: 'commit' });
    await page.waitForFunction(() => performance.getEntriesByName('first-contentful-paint').length > 0);
    const control = page.locator(`#nd-nav [data-header-item="${item}"]`);
    await expect(control).toBeVisible();
    await expect(page.locator('#nd-nav [data-header-item="version"]')).toHaveCount(0);
    await expect(page.locator('#nd-nav [data-header-item="github"]')).toHaveCount(0);
    const before = await control.boundingBox();
    documentResponse!.end(html.slice(split));
    await expect(page.locator('#nd-nav [data-header-item="version"]')).toBeVisible();
    await expect(page.locator('#nd-nav [data-header-item="github"]')).toBeVisible();
    await page.waitForLoadState();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    expect(await control.boundingBox()).toEqual(before);
    await expect(page.locator('html[data-header-shift]')).toHaveCount(0);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});

test('docs sidebar, content and TOC align to the shared header grid', async ({ page }) => {
  for (const width of [768, 1024, 1280, 1440, 1920, 2560, 3440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ['/docs', '/docs/install']) {
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      const edges = await page.evaluate(() => {
        const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
        const article = document.querySelector('#nd-page')!;
        const nav = rect('#nd-nav > nav');
        const layout = rect('#nd-notebook-layout');
        const toc = rect('#nd-toc');
        return {
          logo: rect('[data-header-item="brand"]').left,
          sidebar: rect('#nd-sidebar a').left,
          heading: rect('#nd-page h1').left,
          content: article.getBoundingClientRect().left + parseFloat(getComputedStyle(article).paddingInlineStart),
          body: rect('.swb-mdx').left,
          tocRight: toc.width ? toc.right : null,
          contentRight: rect('#nd-page').right,
          headerRight: nav.right - parseFloat(getComputedStyle(document.querySelector('#nd-nav > nav')!).paddingInlineEnd),
          containerOffsets: [layout.left - nav.left, layout.right - nav.right],
        };
      });
      const at = `${path} at ${width}px`;
      expect(Math.abs(edges.logo - edges.sidebar), `${at} sidebar edge`).toBeLessThanOrEqual(1);
      expect(Math.abs(edges.heading - edges.content), `${at} heading edge`).toBeLessThanOrEqual(1);
      expect(Math.abs(edges.heading - edges.body), `${at} body edge`).toBeLessThanOrEqual(1);
      for (const offset of edges.containerOffsets) expect(Math.abs(offset), `${at} container edge`).toBeLessThanOrEqual(1);
      expect(Math.abs((edges.tocRight ?? edges.contentRight) - edges.headerRight), `${at} right edge`).toBeLessThanOrEqual(1);
    }
  }
});

test('the docs sidebar first item aligns with the content title at scroll start', async ({ page }) => {
  for (const width of [768, 1024, 1280, 1440, 1920, 2560, 3440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ['/docs', '/docs/install', '/docs/setup/claude']) {
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      const { sidebar, heading, toc } = await page.evaluate(() => {
        const top = (selector: string) => {
          const box = document.querySelector(selector)?.getBoundingClientRect();
          return box?.width ? box.top : null;
        };
        const firstLink = top('#nd-sidebar a');
        const viewport = document.querySelector('#nd-sidebar [data-radix-scroll-area-viewport]');
        return {
          sidebar: firstLink === null ? null : firstLink + (viewport?.scrollTop ?? 0),
          heading: top('#nd-page h1'),
          toc: top('#toc-title'),
        };
      });
      expect(sidebar).not.toBeNull();
      expect(heading).not.toBeNull();
      expect(Math.abs(sidebar! - heading!), `${path} at ${width}px sidebar top`).toBeLessThanOrEqual(8);
      if (toc !== null) expect(Math.abs(toc - heading!), `${path} at ${width}px TOC top`).toBeLessThanOrEqual(8);
    }
  }
});

test('the mobile docs drawer opens, navigates and closes inside the shared grid', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/docs/install');
  const brand = await page.locator('[data-header-item="brand"]').boundingBox();
  const title = await page.locator('#nd-page h1').boundingBox();
  expect(Math.abs(brand!.x - title!.x)).toBeLessThanOrEqual(1);
  const drawer = page.locator('#nd-sidebar-mobile');
  await expect(drawer).toBeHidden();
  await page.getByRole('button', { name: 'Open Sidebar', exact: true }).click();
  const overview = drawer.getByRole('link', { name: 'SwB', exact: true });
  await overview.scrollIntoViewIfNeeded();
  await expect(overview).toBeInViewport();
  await overview.click();
  await expect(page).toHaveURL(/\/docs$/);
  await expect(drawer).toBeHidden();
});

test('the desktop Ask AI icon opens the chat from the docs actions', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/docs/install');
  const ask = page.locator('[data-docs-actions] button:has(.lucide-message-circle)');
  await expect(ask).toHaveAccessibleName('Ask AI');
  await expect(ask).toBeInViewport();
  await ask.click();
  await expect(ask).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(ask).toHaveAttribute('aria-expanded', 'false');
});

test('the mobile Ask AI icon never overlaps docs text while scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const prefix of ['', '/zh-Hans', '/zh-Hant']) {
    for (const slug of ['about', 'install', 'deploy']) {
      const path = `${prefix}/docs/${slug}`;
      await page.goto(path);
      await page.evaluate(() => document.fonts.ready);
      const ask = page.locator('[data-docs-actions] button:has(.lucide-message-circle)');
      await expect(ask).toBeInViewport();
      await expect(ask).toHaveCSS('position', 'static');
      const collision = await ask.evaluate(async (control) => {
        const elements = Array.from(document.querySelectorAll('#nd-page :is(p, li, pre, table, h1, h2, h3)'));
        const end = document.documentElement.scrollHeight - window.innerHeight;
        const step = Math.round(window.innerHeight / 4);
        for (let y = 0; y <= end + step; y += step) {
          window.scrollTo(0, Math.min(y, end));
          await new Promise(requestAnimationFrame);
          const icon = control.getBoundingClientRect();
          const hit = elements.find((element) => {
            const rect = element.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && icon.left < rect.right && rect.left < icon.right
              && icon.top < rect.bottom && rect.top < icon.bottom;
          });
          if (hit) return { top: window.scrollY, tag: hit.tagName, text: hit.textContent?.trim().slice(0, 80) };
        }
        return null;
      });
      expect(collision, path).toBeNull();
    }
  }
  const ask = page.locator('[data-docs-actions] button:has(.lucide-message-circle)');
  await ask.click();
  await expect(ask).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(ask).toHaveAttribute('aria-expanded', 'false');
});

test('the open-source statement is visible in all landing heroes', async ({ page }) => {
  for (const [path, text] of [
    ['/', 'Free and open source under Apache-2.0'],
    ['/zh-Hans', '采用 Apache-2.0 许可证，免费开源'],
    ['/zh-Hant', '採用 Apache-2.0 授權，免費開源'],
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
    ['/', 'Set up with agent'], ['/zh-Hans', '让智能体设置'], ['/zh-Hant', '讓代理設定'],
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
      if (chip.top >= github.bottom) {
        expect(inDomOrder).toBe(true);
        expect(license.top).toBeGreaterThan(github.bottom);
        expect(chip.top).toBeGreaterThan(license.bottom);
        expect(Math.abs(setup.left - chip.left)).toBeLessThanOrEqual(1);
        expect(license.top - github.bottom).toBeCloseTo(chip.top - license.bottom, 0);
      } else {
        expect(width).not.toBe(390);
        expect(chip.left).toBeGreaterThan(github.right);
        expect(license.top).toBeGreaterThan(Math.max(chip.bottom, github.bottom));
        expect(Math.abs((chip.top + chip.bottom) / 2 - (github.top + github.bottom) / 2)).toBeLessThanOrEqual(1);
      }
      expect(Math.abs(setup.left - license.left)).toBeLessThanOrEqual(1);
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

test('commands, keycaps and docs previews share one preloaded mono stack', async ({ page }) => {
  const stacks: string[] = [];
  for (const [path, selectors] of [
    ['/', ['[data-install-chip] code', '[data-deploy-option] pre', '[data-search-full] kbd']],
    ['/docs/get-started', ['[data-start-run="local"] pre']],
    ['/docs/setup/claude', ['.swb-prompt pre', 'figure.shiki pre']],
  ] as const) {
    await page.goto(path);
    await expect(page.locator('link[rel="preload"][as="font"][href*="jetbrains_mono"]')).toHaveCount(1);
    for (const selector of selectors) {
      stacks.push(await page.locator(selector).first().evaluate((node) => getComputedStyle(node).fontFamily));
    }
  }
  expect(new Set(stacks).size).toBe(1);
  expect(stacks[0]).toMatch(/jetbrainsMono.+ui-monospace.+monospace/i);
});

test('every shared action has a leading 16px icon', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const path of ['/', '/docs', '/docs/install', '/docs/deploy', '/docs/setup', '/docs/setup/claude']) {
    await page.goto(path);
    const actions = page.locator('.swb-button:visible');
    const assertIcons = async () => {
      expect(await actions.count()).toBeGreaterThan(0);
      for (const action of await actions.all()) {
        const icon = action.locator(':scope > svg').first();
        await expect(icon, await action.innerText()).toHaveCSS('width', '16px');
        await expect(icon).toHaveCSS('height', '16px');
        if (await action.evaluate((node) => getComputedStyle(node).fontSize === '0px')) {
          const buttonBox = (await action.boundingBox())!;
          const iconBox = (await icon.boundingBox())!;
          expect(Math.abs((buttonBox.x + buttonBox.width / 2) - (iconBox.x + iconBox.width / 2))).toBeLessThanOrEqual(1);
          continue;
        }
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
    'href', 'https://github.com/ZhehanZhang/SwB/blob/release/1.0/docs/en/install.mdx',
  );
});

test('landing shows supported deployment links and six usage examples in each locale', async ({ page }) => {
  for (const [prefix, deployGuide, allowance, paid, hobby] of [
    ['', 'Deploy guide', 'Free with monthly allowance', 'Paid plan', 'Hobby plan'],
    ['/zh-Hans', '部署指南', '每月额度内免费', '付费套餐', 'Hobby 套餐'],
    ['/zh-Hant', '部署指南', '每月額度內免費', '付費方案', 'Hobby 方案'],
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
    await expect(fly).toHaveAttribute('href', `${prefix}/docs/deploy#fly`);
    await expect(fly).toHaveClass((await render.getAttribute('class'))!);
    const tunnel = deploy.locator('[data-deploy-tunnel]');
    await expect(tunnel).toHaveAttribute('href', `${prefix}/docs/deploy#cloudflare-tunnel`);
    await expect(tunnel).toHaveCSS('border-radius', '6px');
    await expect(deploy.locator(`a[href="${prefix}/docs/install"] > .lucide-book-open`)).toHaveCount(1);
    await expect(deploy.locator(`a[href="${prefix}/docs/deploy#docker-compose"] > .lucide-book-open`)).toHaveCount(1);
    const usage = page.locator('#landing-usage').locator('..');
    await expect(usage.locator('p')).toHaveCount(6);
    await expect(deploy.getByRole('link', { name: deployGuide })).toHaveAttribute('href', `${prefix}/docs/deploy`);
    await fly.click();
    await expect(page).toHaveURL(`${prefix}/docs/deploy#fly`);
    const headings = page.locator('.swb-mdx').getByRole('heading');
    await expect(headings.and(page.locator('#docker-compose'))).toContainText('Docker Compose');
    await expect(headings.and(page.locator('#fly'))).toContainText('Fly.io');
    await expect(headings.and(page.locator('#railway'))).toContainText('Railway');
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

test('deploy columns stack below 900px and align each row on wider screens', async ({ page }) => {
  for (const path of ['/', '/zh-Hans', '/zh-Hant', '/es', '/fr', '/de', '/ko', '/ja']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    const grid = page.locator('[data-deploy-grid]');
    for (const width of [390, 899, 900, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const layout = await grid.evaluate((node) => {
        const blocks = [...node.children];
        const edges = (element: Element) => {
          const { top, bottom } = element.getBoundingClientRect();
          return { top, bottom };
        };
        const [uvx, compose] = [...node.querySelectorAll('[data-deploy-primary] pre')];
        const composeStyle = getComputedStyle(compose);
        const composeChrome = [composeStyle.paddingBlockStart, composeStyle.paddingBlockEnd,
          composeStyle.borderBlockStartWidth, composeStyle.borderBlockEndWidth]
          .reduce((sum, length) => sum + parseFloat(length), 0);
        const center = (element: Element) => {
          const { top, bottom } = edges(element);
          return (top + bottom) / 2;
        };
        return {
          columns: getComputedStyle(node).gridTemplateColumns.split(' '),
          options: blocks.map(edges),
          intro: blocks.map((block) => edges(block.querySelector('p')!)),
          primary: blocks.map((block) => edges(block.querySelector('[data-deploy-primary] > *')!)),
          secondary: blocks.map((block) => edges(block.querySelector('[data-deploy-secondary] :is(summary,a)')!)),
          internalGaps: blocks.map((block) => [...block.children]
            .map(edges).slice(1).map(({ top }, index) => top - edges(block.children[index]).bottom)),
          composeHeight: compose.getBoundingClientRect().height,
          composeNaturalHeight: composeChrome + parseFloat(composeStyle.lineHeight) * compose.textContent!.split('\n').length,
          uvxCenterDelta: Math.abs(center(uvx.querySelector('code')!) - center(uvx)),
        };
      });
      expect(layout.columns).toHaveLength(width < 900 ? 1 : 3);
      expect(layout.options).toHaveLength(3);
      for (const { top, bottom } of layout.intro) expect(bottom - top, `${path} at ${width}px intro`).toBe(20);
      expect(layout.composeHeight, `${path} at ${width}px compose height`).toBe(layout.composeNaturalHeight);
      expect(layout.uvxCenterDelta, `${path} at ${width}px uvx center`).toBeLessThanOrEqual(1);
      if (width >= 900) {
        for (const row of ['options', 'intro', 'primary', 'secondary'] as const) {
          for (const edge of ['top', 'bottom'] as const) {
            const values = layout[row].map((box) => box[edge]);
            expect(Math.max(...values) - Math.min(...values), `${path} at ${width}px ${row} ${edge}`).toBe(0);
          }
        }
      } else {
        expect(layout.options[1].top - layout.options[0].bottom).toBe(24);
        expect(layout.options[2].top - layout.options[1].bottom).toBe(24);
      }
      for (const gap of layout.internalGaps.flat()) expect(gap).toBe(8);
    }
  }
});

test('provider and secondary rows fit one line in English and Chinese', async ({ page }) => {
  for (const path of ['/', '/zh-Hans', '/zh-Hant']) {
    await page.goto(path);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const rows = await page.locator('[data-deploy-providers] a, [data-deploy-secondary] :is(summary,a)').evaluateAll((links) => links.map((link) => {
        const { width, height, right } = link.getBoundingClientRect();
        const style = getComputedStyle(link);
        return {
          width, height,
          contained: link.lastElementChild!.getBoundingClientRect().right <= right - parseFloat(style.paddingInlineEnd) + 1,
          labelHeight: link.children[1].getBoundingClientRect().height,
          whiteSpace: style.whiteSpace,
          isProvider: link.closest('[data-deploy-providers]') !== null,
        };
      }));
      expect(rows).toHaveLength(6);
      const cloud = rows.filter((row) => row.isProvider);
      expect(new Set(cloud.map(({ width }) => width)).size).toBe(1);
      for (const row of rows) {
        expect(row.whiteSpace).toBe('nowrap');
        expect(row.contained).toBe(true);
        expect(row.height, `${path} action height at ${width}px`).toBeCloseTo(row.isProvider && width >= 900 ? (108 - 2) / 3 : 44, 1);
        expect(row.labelHeight).toBe(20);
      }
    }
  }
});
