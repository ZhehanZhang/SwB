import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { locales, localize } from '../../lib/i18n';
import { observeLayoutShifts, readLayoutShifts } from './layout-shifts';

const origin = 'https://my-swb.example.net:9443';
const example = 'https://swb.example.com';
const start = '/docs/get-started';
const claude = '/docs/setup/claude';
const install = '/docs/install';
const deploy = '/docs/deploy';
const cloudflare = '/docs/cloudflare';
const connect = '/docs/connect';
const addressKey = 'swb-address';
const label = 'Your SwB address';
const promptDirectory = resolve(__dirname, '../../../docs/prompts');

const docs = (page: Page) => page.locator('.swb-mdx');
const address = (page: Page) => page.locator('[data-get-started]').getByRole('textbox', { name: label });
const indicator = (page: Page) => page.locator('#nd-nav').getByRole('button', { name: label });
const clipboard = (page: Page) => page.evaluate(() => navigator.clipboard.readText());
const stored = (page: Page) => page.evaluate((key) => localStorage.getItem(key), addressKey);

async function openGetStarted(page: Page) {
  await page.goto(`${start}?agents=claude`);
  await expect(page.locator('[data-get-started]')).toHaveAttribute('data-ready', 'true');
}

async function copyBlock(page: Page, value: string) {
  const code = docs(page).locator('figure.shiki').filter({ hasText: value });
  await expect(code.locator('pre')).toContainText(value);
  await code.getByRole('button', { name: 'Copy Text' }).click();
  expect(await clipboard(page)).toBe(value);
}

test('persists one address for Get started, docs, copy and Open in actions', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });

  await openGetStarted(page);
  await expect(indicator(page)).toHaveCount(0);
  await address(page).fill(`${origin}/mcp`);
  await address(page).press('Enter');
  expect(await stored(page)).toBe(origin);
  await page.reload();
  await expect(address(page)).toHaveValue(origin);
  const prompt = readFileSync(resolve(promptDirectory, 'claude.md'), 'utf8').replaceAll(example, origin);
  const getStarted = page.getByRole('region', { name: 'Claude', exact: true });
  await expect(getStarted.locator('pre')).toHaveText(prompt);
  await getStarted.getByRole('button', { name: 'Copy prompt' }).click();
  expect(await clipboard(page)).toBe(prompt);
  const open = await getStarted.getByRole('link', { name: 'Open in Claude' }).getAttribute('href');
  expect(new URL(open!).searchParams.get('q')).toBe(prompt);

  await page.goto(claude);
  const setup = docs(page).locator('figure.swb-prompt');
  await expect(setup.locator('pre')).toContainText(`${origin}/mcp`);
  for (const app of ['Claude', 'ChatGPT']) {
    const url = await setup.getByRole('link', { name: `Open in ${app}` }).getAttribute('href');
    expect(new URL(url!).searchParams.get('q')).toBe(await setup.locator('pre').textContent());
  }
  await setup.getByRole('button', { name: 'Copy prompt' }).click();
  expect(await clipboard(page)).toBe(await setup.locator('pre').textContent());
  await copyBlock(page, `${origin}/mcp`);
  await docs(page).getByRole('tab', { name: 'Team or Enterprise' }).click();
  await docs(page).getByRole('tab', { name: 'Personal plan' }).click();
  await expect(docs(page).locator('figure.shiki code')).toHaveText(`${origin}/mcp`);

  await page.goto(install);
  await copyBlock(page, `curl -fsS ${origin}/healthz`);
  await expect(docs(page).locator('pre').filter({ hasText: 'http://127.0.0.1:8870/healthz' })).toBeVisible();
  await page.evaluate(() => Object.assign(window, { swbSoftNavigation: true }));
  await docs(page).getByRole('link', { name: 'Docker Compose' }).click();
  await expect(page).toHaveURL(`${deploy}#docker-compose`);
  expect(await page.evaluate(() => Reflect.get(window, 'swbSoftNavigation'))).toBe(true);
  await docs(page).getByRole('link', { name: 'Cloudflare', exact: true }).click();
  await expect(page).toHaveURL(cloudflare);
  await copyBlock(page, `SWB_PUBLIC_URL=${origin}`);
  await expect(docs(page).getByRole('link', { name: /^check the public address$/i })).toHaveAttribute('href', `${origin}/healthz`);

  await page.goto(connect);
  await expect(docs(page).getByText(`${origin}/mcp`, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Copy Markdown' }).click();
  await expect.poll(() => clipboard(page)).toContain(`claude mcp add --transport http --scope user swb ${origin}/mcp`);

  await page.goto('/docs/setup/coding-agent');
  await docs(page).getByRole('tab', { name: 'Codex' }).click();
  await copyBlock(page, `[mcp_servers.swb]\nurl = "${origin}/mcp"`);
  expect(errors).toEqual([]);
});

test('copies a fragment link, edits from the popover and clears the address everywhere', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`${cloudflare}?tab=remote#named-tunnel&swb=${encodeURIComponent(origin)}`);
  await expect(page).toHaveURL(`${cloudflare}?tab=remote#named-tunnel`);
  await indicator(page).click();
  const popover = page.getByRole('dialog');
  await expect(popover.getByRole('textbox', { name: label })).toHaveValue(origin);
  await popover.getByRole('button', { name: 'Copy link with your address' }).click();
  const share = new URL(await clipboard(page));
  expect(share.search).toBe('?tab=remote');
  expect(share.hash).toBe(`#named-tunnel&swb=${encodeURIComponent(origin)}`);

  const update = 'https://other-swb.example.org';
  await popover.getByRole('textbox', { name: label }).fill(update);
  await popover.getByRole('textbox', { name: label }).press('Enter');
  await expect(docs(page).getByRole('link', { name: /^check the public address$/i })).toHaveAttribute('href', `${update}/healthz`);
  await popover.getByRole('button', { name: 'Clear' }).click();
  await expect(indicator(page)).toHaveCount(0);
  await expect(docs(page).getByRole('link', { name: /^check the public address$/i })).toHaveAttribute('href', `${example}/healthz`);
  await copyBlock(page, `SWB_PUBLIC_URL=${example}`);
  expect(await stored(page)).toBeNull();
  await openGetStarted(page);
  await expect(address(page)).toHaveValue('');
  await expect(docs(page).getByRole('region', { name: 'Claude', exact: true }).locator('pre')).toContainText(example);
});

test('a shared Deploy section link stores the address and scrolls to the heading', async ({ page }) => {
  await page.goto(`${deploy}#fly&swb=${encodeURIComponent(origin)}`);
  await expect(page).toHaveURL(`${deploy}#fly`);
  await expect.poll(() => stored(page)).toBe(origin);
  const heading = docs(page).locator('#fly');
  await expect(heading).toBeInViewport({ ratio: 1 });
  await expect.poll(() => heading.evaluate((node) =>
    Math.abs(node.getBoundingClientRect().top - parseFloat(getComputedStyle(node).scrollMarginTop))
  )).toBeLessThanOrEqual(1);
});

test('address trigger matches the language control and its aligned popover saves explicitly', async ({ page }) => {
  await page.goto(`${install}?swb=${encodeURIComponent(origin)}`);
  const trigger = indicator(page);
  await expect(trigger).toHaveAccessibleName(`${label} ${new URL(origin).host}`);
  const dimensions = await page.locator('[data-header-item="address"], [data-header-item="language"]').evaluateAll((buttons) => buttons.map((button) => {
    const style = getComputedStyle(button);
    const svg = button.querySelector('svg')!;
    return {
      height: style.height, width: style.width, radius: style.borderRadius, padding: style.padding,
      icon: svg.getBoundingClientRect().width, stroke: getComputedStyle(svg).strokeWidth,
    };
  }));
  expect(dimensions).toHaveLength(2);
  expect(dimensions[0]).toEqual(dimensions[1]);
  await trigger.click();

  const popover = page.getByRole('dialog', { name: label });
  const input = popover.getByRole('textbox', { name: label });
  const save = popover.getByRole('button', { name: 'Save' });
  const clear = popover.getByRole('button', { name: 'Clear' });
  const copy = popover.getByRole('button', { name: 'Copy link with your address' });
  await expect(popover).toBeFocused();
  await expect(input).toHaveValue(origin);
  await popover.evaluate((panel) => Promise.all(panel.getAnimations().map((animation) => animation.finished)));
  const [buttonBox, panelBox, inputBox, clearBox, copyBox, saveBox] = await Promise.all(
    [trigger, popover, input, clear, copy, save].map((item) => item.boundingBox()),
  );
  const width = page.viewportSize()!.width;
  const panelRight = panelBox!.x + panelBox!.width;
  if (width === 390) {
    expect(Math.abs(panelBox!.x - 20)).toBeLessThanOrEqual(1);
    expect(Math.abs(panelRight - width + 20)).toBeLessThanOrEqual(1);
  } else {
    expect(Math.abs(panelRight - buttonBox!.x - buttonBox!.width)).toBeLessThanOrEqual(1);
  }
  expect(Math.abs(clearBox!.x - inputBox!.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(saveBox!.x + saveBox!.width - inputBox!.x - inputBox!.width)).toBeLessThanOrEqual(1);
  expect(Math.max(clearBox!.y, copyBox!.y, saveBox!.y) - Math.min(clearBox!.y, copyBox!.y, saveBox!.y)).toBeLessThanOrEqual(1);

  await page.keyboard.press('Tab');
  await expect(input).toBeFocused();
  await input.fill('example');
  await save.click();
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  expect(await stored(page)).toBe(origin);
  const update = 'https://new-swb.example.org';
  await input.fill(`${update}/mcp`);
  await input.press('Tab');
  expect(await stored(page)).toBe(origin);
  await save.click();
  await expect(input).toHaveValue(update);
  expect(await stored(page)).toBe(update);
});

test('accepts fragment and query prefill and preserves anchors and picker state', async ({ page }) => {
  await page.goto(`${claude}?swb=${encodeURIComponent(`${origin}/mcp`)}#copy-the-setup-prompt`);
  await expect(page).toHaveURL(`${claude}#copy-the-setup-prompt`);
  await expect(indicator(page)).toHaveAttribute('title', 'my-swb.example.net:9443');
  expect(await stored(page)).toBe(origin);

  const second = 'https://next-swb.example.org';
  await page.goto(`${start}?agents=claude&run=server#swb=${encodeURIComponent(second)}&claude`);
  await expect(page).toHaveURL(`${start}?agents=claude&run=server#claude`);
  await expect(address(page)).toHaveValue(second);
  await expect(docs(page).getByRole('button', { name: 'On your server' })).toHaveAttribute('aria-pressed', 'true');
  await expect(docs(page).getByRole('button', { name: 'Claude', exact: true })).toHaveAttribute('aria-pressed', 'true');

  await page.evaluate((value) => { location.hash = `swb=${encodeURIComponent(value)}&claude`; }, origin);
  await expect(page).toHaveURL(`${start}?agents=claude&run=server#claude`);
  await expect(address(page)).toHaveValue(origin);
});

test('prefills and fits the address panel in all eight locales', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const locale of locales) {
    await page.goto(`${localize(locale, install)}?swb=${encodeURIComponent(origin)}`);
    const trigger = page.locator('[data-header-item="address"]');
    await expect(trigger).toHaveAttribute('title', new URL(origin).host);
    await expect(page).toHaveURL(localize(locale, install));
    await trigger.click();
    const panel = page.getByRole('dialog');
    await expect(panel.getByRole('textbox')).toHaveValue(origin);
    const overflow = await panel.evaluate((element) => {
      const { left, right } = element.getBoundingClientRect();
      return Math.max(20 - left, right - innerWidth + 20, ...[...element.querySelectorAll('button')].map((button) => {
        const rect = button.getBoundingClientRect();
        return Math.max(left - rect.left, rect.right - right);
      }));
    });
    expect(overflow, locale).toBeLessThanOrEqual(1);
    await expect(panel.getByRole('button')).toHaveCount(3);
  }
});

test('rejects hostile field, fragment and query input without changing the saved address', async ({ page }) => {
  const dialogs: string[] = [];
  page.on('dialog', async (dialog) => { dialogs.push(dialog.message()); await dialog.dismiss(); });
  await page.goto(`${start}?agents=claude#swb=${encodeURIComponent(origin)}`);
  await expect(address(page)).toHaveValue(origin);
  for (const value of ['javascript:alert(1)', 'https://user:pw@x.com', 'https://x.com"><script>alert(1)</script>', 'https://swb。example.net', 'http://example.com']) {
    await address(page).fill(value);
    await address(page).press('Enter');
    await expect(address(page)).toHaveAttribute('aria-invalid', 'true');
    expect(await stored(page)).toBe(origin);
    for (const location of [`${claude}#swb=${encodeURIComponent(value)}`, `${claude}?swb=${encodeURIComponent(value)}`]) {
      await page.goto(location);
      await expect(indicator(page)).toHaveAttribute('title', 'my-swb.example.net:9443');
      await expect(page).toHaveURL(location);
      expect(await stored(page)).toBe(origin);
    }
    await openGetStarted(page);
  }
  await address(page).fill('');
  await address(page).press('Enter');
  expect(await stored(page)).toBe(origin);
  expect(dialogs).toEqual([]);
});

test('keeps server HTML, RSC, Markdown, llms text and all 18 prompt files canonical', async ({ request }) => {
  const html = await request.get(`${claude}?swb=${encodeURIComponent(origin)}`);
  expect(html.ok()).toBe(true);
  expect(await html.text()).toContain(`${example}/mcp`);
  expect(await html.text()).not.toContain(origin);
  const rsc = await request.get(`${claude}?swb=${encodeURIComponent(origin)}`, { headers: { RSC: '1' } });
  expect(rsc.headers()['content-type']).toContain('text/x-component');
  expect(await rsc.text()).toContain(`${example}/mcp`);
  expect(await rsc.text()).not.toContain(origin);
  for (const path of [`${claude}.mdx`, '/llms-full.txt']) {
    const response = await request.get(`${path}?swb=${encodeURIComponent(origin)}`);
    expect(response.ok()).toBe(true);
    expect(await response.text()).toContain(`${example}/mcp`);
    expect(await response.text()).not.toContain(origin);
  }
  const index = await request.get('/llms.txt');
  const sharedIndex = await request.get(`/llms.txt?swb=${encodeURIComponent(origin)}`);
  expect(await sharedIndex.text()).toBe(await index.text());
  expect(await index.text()).toContain('/prompts/claude.md');
  const files = readdirSync(promptDirectory).filter((file) => file.endsWith('.md'));
  expect(files).toHaveLength(18);
  for (const file of files) {
    const response = await request.get(`/prompts/${file}?swb=${encodeURIComponent(origin)}`);
    expect(response.ok()).toBe(true);
    expect((await response.body()).equals(readFileSync(resolve(promptDirectory, file))), file).toBe(true);
  }
});

test('long saved addresses leave docs CLS below 0.01', async ({ page }) => {
  const long = `https://${'a'.repeat(50)}.${'b'.repeat(50)}.${'c'.repeat(50)}.example.org:9443`;
  await page.addInitScript(observeLayoutShifts);
  await page.addInitScript(({ key, address }) => localStorage.setItem(key, address), { key: addressKey, address: long });
  for (const path of [claude, deploy, cloudflare, connect]) {
    await page.goto(path);
    await expect(indicator(page)).toHaveAttribute('title', new URL(long).host);
    if (path !== deploy) {
      await expect(docs(page).getByText(new RegExp(long.replaceAll('.', '\\.'))).first()).toBeVisible();
    }
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    const shifts = await page.evaluate(readLayoutShifts);
    const total = shifts.reduce((sum, { value }) => sum + value, 0);
    console.info(`${path} CLS ${total}`);
    expect(total, `${path}\n${JSON.stringify(shifts)}`).toBeLessThan(0.01);
  }
});

test('reserves the desktop TOC column before its markup arrives', async ({ page }) => {
  const layout = () => page.locator('.swb-mdx').evaluate((article) => {
    const { x, width } = article.getBoundingClientRect();
    return { x, width };
  });
  await page.goto(claude);
  const complete = await layout();
  await page.route('**/docs/setup/claude', async (route) => {
    const response = await route.fetch();
    const html = await response.text();
    expect(html).toContain('xl:layout:[--fd-toc-width:268px]');
    await route.fulfill({ response, body: html.replaceAll('xl:layout:[--fd-toc-width:268px]', '') });
  });
  await page.route('**/_next/static/**/*.js', (route) => route.abort());
  await page.reload();
  expect(await layout()).toEqual(complete);
});

test('adds the existing Webhooks prompt and guide to the picker', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`${start}?agents=webhook`);
  await expect(docs(page).getByRole('button', { name: 'Webhooks' })).toHaveAttribute('aria-pressed', 'true');
  const section = docs(page).getByRole('region', { name: 'Webhooks' });
  await expect(section.getByRole('link', { name: 'Full guide' })).toHaveAttribute('href', '/docs/blocks/webhooks');
  await section.getByRole('button', { name: 'Copy prompt' }).click();
  expect(await clipboard(page)).toBe(readFileSync(resolve(promptDirectory, 'webhook.md'), 'utf8'));
});
