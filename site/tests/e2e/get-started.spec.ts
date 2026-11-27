import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';

const start = '/docs/get-started';
const selfHosting = '/docs/self-hosting';
const renderBlueprint = 'https://render.com/deploy?repo=https://github.com/ZhehanZhang/SwB/tree/release/1.0';
const railwayTemplate = 'https://railway.com/deploy/swb';
const fallbackAgents = [
  'Claude Project', 'Claude', 'ChatGPT', 'ChatGPT dots', 'Muse', 'Grok', 'Grok Bot', 'Capy Captain',
  'Claude Code', 'Codex', 'Gemini CLI', 'Slack', 'Discord', 'Telegram', 'GitHub',
];

const setup = (page: Page) => page.locator('[data-get-started]');
const tile = (page: Page, name: string) => setup(page).getByRole('button', { name, exact: true });
const agent = (page: Page, name: string) => setup(page).getByRole('region', { name, exact: true });
const sourcePrompt = (name: string) => readFileSync(resolve(__dirname, `../../../docs/prompts/${name}.md`), 'utf8');

test('preselects the URL order and scrolls to the first agent', async ({ page }) => {
  await page.goto(`${start}?agents=codex,claude&run=server`);
  await expect(tile(page, 'Codex')).toHaveAttribute('aria-pressed', 'true');
  await expect(tile(page, 'Claude')).toHaveAttribute('aria-pressed', 'true');
  await expect(tile(page, 'On your server')).toHaveAttribute('aria-pressed', 'true');
  await expect(setup(page).getByRole('heading', { level: 3 })).toHaveText(['Codex', 'Claude']);
  await expect(agent(page, 'Codex')).toBeInViewport({ ratio: 0.5 });
  await expect.poll(() => agent(page, 'Codex').evaluate((node) => node.getBoundingClientRect().top)).toBeLessThan(150);
});

test('toggles agent sections in selection order and keeps the controls in place', async ({ page }) => {
  await page.goto(start);
  const heading = setup(page).getByRole('heading', { name: 'Pick your agents' });
  const sections = setup(page).getByRole('heading', { level: 3 });
  await expect(sections).toHaveCount(0);
  const top = await heading.evaluate((node) => node.getBoundingClientRect().top + scrollY);

  await tile(page, 'Codex').click();
  await tile(page, 'Claude').click();
  await expect(sections).toHaveText(['Codex', 'Claude']);
  await expect(page).toHaveURL(/agents=codex,claude&run=local$/);
  await tile(page, 'Codex').click();
  await expect(tile(page, 'Codex')).toHaveAttribute('aria-pressed', 'false');
  await expect(sections).toHaveText(['Claude']);
  await tile(page, 'Codex').click();
  await expect(sections).toHaveText(['Claude', 'Codex']);
  expect(await heading.evaluate((node) => node.getBoundingClientRect().top + scrollY)).toBe(top);
});

test('switches run locations and copies the release install commands', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(start);
  const run = (kind: string) => setup(page).locator(`[data-start-run="${kind}"]`);
  const read = () => page.evaluate(() => navigator.clipboard.readText());
  await expect(tile(page, 'On your computer')).toHaveAttribute('aria-pressed', 'true');
  await expect(run('local').getByText('uvx swb-one up', { exact: true })).toBeVisible();
  await run('local').getByRole('button', { name: 'Copy command', exact: true }).click();
  expect(await read()).toBe('uvx swb-one up');
  await run('local').getByText('Use pipx', { exact: true }).click();
  await run('local').getByRole('button', { name: 'Copy command', exact: true }).last().click();
  expect(await read()).toBe('pipx install swb-one\nswb up');

  await tile(page, 'On your server').click();
  await expect(run('local')).toBeHidden();
  await expect(run('server').getByRole('link', { name: 'Reach it from anywhere' }))
    .toHaveAttribute('href', `${selfHosting}#cloudflare-tunnel`);
  await expect(run('server').locator('image[href="/marks/cloudflare-on-light.svg"]')).toBeVisible();
  await run('server').getByRole('button', { name: 'Copy command', exact: true }).click();
  const selfHostingSource = readFileSync(resolve(__dirname, '../../../docs/en/self-hosting.mdx'), 'utf8');
  expect(await read()).toBe(selfHostingSource.split('```bash\n')[1].split('\n```')[0]);
  await expect(page).toHaveURL(/run=server$/);

  await tile(page, 'In the cloud').click();
  await expect(run('server')).toBeHidden();
  await expect(run('cloud').getByRole('link', { name: 'Fly.io Free with monthly allowance' }))
    .toHaveAttribute('href', `${selfHosting}#fly`);
  await expect(run('cloud').getByRole('link', { name: 'Render Paid plan' })).toHaveAttribute('href', renderBlueprint);
  await expect(run('cloud').getByRole('link', { name: 'Railway Hobby plan' })).toHaveAttribute('href', railwayTemplate);
  for (const provider of ['fly', 'render', 'railway']) {
    await expect(run('cloud').locator(`image[href="/marks/${provider}-on-light.svg"]`)).toBeVisible();
  }
  await expect(page).toHaveURL(/run=cloud$/);
});

test('browser Back leaves the flow and Forward restores the replaced URL', async ({ page }) => {
  await page.goto('/docs');
  await page.locator('a.swb-button-primary[href="/docs/get-started"]').click();
  await expect(page).toHaveURL(`/docs/get-started`);
  const length = await page.evaluate(() => history.length);
  await tile(page, 'Claude').click();
  await tile(page, 'On your server').click();
  expect(await page.evaluate(() => history.length)).toBe(length);

  await page.goBack();
  await expect(page).toHaveURL('/docs');
  await page.goForward();
  await expect(page).toHaveURL(/agents=claude&run=server$/);
  await expect(tile(page, 'Claude')).toHaveAttribute('aria-pressed', 'true');
  await expect(tile(page, 'On your server')).toHaveAttribute('aria-pressed', 'true');
});

test('query preselect has zero CLS even with delayed hydration', async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { shifts: [] });
    const shifts = (window as Window & { shifts?: number[] }).shifts!;
    new PerformanceObserver((entries) => {
      for (const item of entries.getEntries()) {
        const shift = item as PerformanceEntry & { hadRecentInput: boolean; value: number };
        if (!shift.hadRecentInput) shifts.push(shift.value);
      }
    }).observe({ type: 'layout-shift', buffered: true });
  });
  await page.route('**/_next/static/chunks/*.js', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 250));
    await route.continue();
  });
  await page.goto(`${start}?agents=codex,claude&run=server`);
  await expect(tile(page, 'Codex')).toHaveAttribute('aria-pressed', 'true');
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  const shifts = await page.evaluate(() => (window as Window & { shifts?: number[] }).shifts);
  expect(shifts?.reduce((total, shift) => total + shift, 0)).toBe(0);
});

test('copies exact source prompt bytes for Claude and Codex', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`${start}?agents=claude,codex`);
  for (const [name, source] of [['Claude', 'claude'], ['Codex', 'coding-agent']]) {
    const section = agent(page, name);
    expect(await section.locator('pre').textContent()).toBe(sourcePrompt(source));
    await section.getByRole('button', { name: 'Copy prompt' }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(sourcePrompt(source));
  }
});

test('recommends Muse gadget pairing before the connector and copies its command', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`${start}?agents=muse`);
  const section = agent(page, 'Muse');
  await expect(section.getByRole('heading', { level: 4 })).toHaveText([
    'Wake Muse with a gadget', 'Connect Muse to SwB tools',
  ]);
  await expect(section.getByText(/single-use code within ten minutes/)).toContainText('Add Device');
  await expect(section.getByText(/single-use code within ten minutes/)).toContainText('Allow Bluetooth');
  await section.getByRole('button', { name: 'Copy command' }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText()))
    .toBe("uvx --python 3.13 --from 'swb-one[companion]' swb companion connect https://swb.example.com --cap muse");
  await expect(section.getByRole('link', { name: 'Full guide' })).toHaveAttribute('href', '/docs/setup/muse');
});

test('explains the prompts and links companion setup without offering it as an agent', async ({ page }) => {
  await page.goto(`${start}?agents=claude-code,codex,muse`);
  await expect(page.getByText(/MCP, an open standard/)).toBeVisible();
  await expect(setup(page).getByText('The prompts below use your SwB address.')).toBeVisible();
  await expect(setup(page).getByText(/Choose where SwB runs to see/)).toBeVisible();
  await expect(tile(page, 'Companion')).toHaveCount(0);
  for (const name of ['Claude Code', 'Codex', 'Muse']) {
    const section = agent(page, name);
    await expect(section.locator('[data-agent-before]')).toContainText(/pairing|pair a companion/);
    await expect(section.locator('[data-agent-after]')).toHaveText(/\S/);
    await expect(section.locator('[data-agent-before]').getByRole('link', { name: 'Companion guide' }))
      .toHaveAttribute('href', '/docs/companion');
  }
});

test('personalizes preview, open link and copy while the source prompt stays unchanged', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(`${start}?agents=claude,codex`);
  const address = setup(page).getByRole('textbox', { name: 'Your SwB address' });
  const origin = 'https://swb.example.net:9443';
  await address.fill(`${origin}/mcp`);
  await address.blur();
  await expect(address).toHaveValue(origin);
  expect(new URL(page.url()).searchParams.get('url')).toBe(origin);

  for (const [name, source] of [['Claude', 'claude'], ['Codex', 'coding-agent']]) {
    const section = agent(page, name);
    const expected = sourcePrompt(source).replace(/(?:https:\/\/)?swb\.example\.com/g, origin);
    expect(await section.locator('pre').textContent()).toBe(expected);
    await section.getByRole('button', { name: 'Copy prompt' }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(expected);
  }
  const open = await agent(page, 'Claude').getByRole('link', { name: 'Open in Claude' }).getAttribute('href');
  expect(new URL(open!).searchParams.get('q')).toBe(sourcePrompt('claude').replaceAll('https://swb.example.com', origin));
  const packaged = await page.request.get('/prompts/claude.md');
  expect(await packaged.text()).toBe(sourcePrompt('claude'));

  await page.goto(`${start}?agents=claude`);
  await expect(address).toHaveValue(origin);
  await address.fill('ftp://swb.example.com');
  await address.blur();
  await expect(address).toHaveAttribute('aria-invalid', 'true');
  expect(new URL(page.url()).searchParams.has('url')).toBe(false);
  expect(await agent(page, 'Claude').locator('pre').textContent()).toBe(sourcePrompt('claude'));

  await page.goto(`${start}?agents=claude&url=${encodeURIComponent(origin)}`);
  await expect(address).toHaveValue(origin);
  await expect(agent(page, 'Claude').locator('pre')).toContainText(origin);
});

test.describe('server fallback', () => {
  test.use({ javaScriptEnabled: false });

  test('shows local setup and every agent in the server HTML', async ({ page }) => {
    await page.goto(`${start}?agents=codex&run=cloud`);
    await expect(tile(page, 'On your computer')).toHaveAttribute('aria-pressed', 'true');
    await expect(setup(page).getByText('uvx swb-one up', { exact: true })).toBeVisible();
    await expect(setup(page).getByRole('heading', { level: 3 })).toHaveText(fallbackAgents);
    await expect(agent(page, 'Codex').locator('pre')).toContainText('codex mcp login swb');
  });
});

for (const [prefix, title, cloud, pick, guide] of [
  ['', 'Get started', 'In the cloud', 'Pick your agents', 'Full guide'],
  ['/zh-Hans', '开始使用', '在云端运行', '选择智能体', '完整指南'],
  ['/zh-Hant', '開始使用', '在雲端執行', '選擇代理', '完整指南'],
]) {
  test(`${prefix.slice(1) || 'en'} presents the localized flow and links`, async ({ page }) => {
    await page.goto(`${prefix}${start}?agents=claude&run=cloud`);
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expect(tile(page, cloud)).toHaveAttribute('aria-pressed', 'true');
    await expect(setup(page).getByRole('heading', { name: pick })).toBeVisible();
    await expect(agent(page, 'Claude').getByRole('link', { name: guide, exact: true }))
      .toHaveAttribute('href', `${prefix}/docs/setup/claude`);
    await expect(setup(page).getByRole('link', { name: /^Fly\.io / }))
      .toHaveAttribute('href', `${prefix}${selfHosting}#fly`);
    await expect(agent(page, 'Claude').locator('[data-agent-before]')).toHaveText(/\S/);
    await expect(agent(page, 'Claude').locator('[data-agent-after]')).toHaveText(/\S/);

    await page.goto(`${prefix}/docs/setup`);
    await expect(page.locator('article p').filter({ hasText: 'MCP' }).first()).toBeVisible();
    await expect(page.locator(`article p a[href="${prefix}${start}"]`)).toHaveText(title);
  });
}

test('Self-hosting keeps one-click command copy and deployment links', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto(selfHosting);
  await expect(page.getByRole('link', { name: 'Render Paid plan' })).toHaveAttribute('href', renderBlueprint);
  await expect(page.getByRole('link', { name: 'Railway Hobby plan' })).toHaveAttribute('href', railwayTemplate);
  const tunnelHeading = page.getByRole('heading', { name: 'Cloudflare Tunnel' });
  await expect(tunnelHeading).toHaveAttribute('id', 'cloudflare-tunnel');
  await expect(tunnelHeading.locator('image[href="/marks/cloudflare-on-light.svg"]')).toBeVisible();
  for (const block of await page.locator('figure.shiki').all()) {
    const copy = block.getByRole('button', { name: 'Copy Text' });
    await expect(copy).toBeVisible();
    await copy.click();
    expect((await page.evaluate(() => navigator.clipboard.readText())).trimEnd())
      .toBe((await block.locator('pre').textContent())?.trimEnd());
  }
});

test('publishes Get started in llms.txt and the sitemap and preserves startup links', async ({ request }) => {
  for (const suffix of ['/llms.txt', '/sitemap.xml']) {
    const response = await request.get(suffix);
    expect(response.ok()).toBe(true);
    const text = await response.text();
    for (const prefix of ['', '/zh-Hans', '/zh-Hant']) expect(text).toContain(`https://swb.one${prefix}${start}`);
  }
  for (const path of ['/docs/first-run', '/docs/install', '/docs/setup/claude']) expect((await request.get(path)).ok()).toBe(true);
});
