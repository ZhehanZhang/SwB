import { expect, test, type Locator } from '@playwright/test';

async function dimensions(locator: Locator) {
  return locator.evaluate((node) => {
    const { x, y, width, height } = node.getBoundingClientRect();
    return [x + window.scrollX, y + window.scrollY, width, height];
  });
}

test('landing and docs share links, with a menu on narrow screens', async ({ page }) => {
  const narrow = page.viewportSize()!.width < 640;
  for (const path of ['/', '/docs']) {
    await page.goto(path);
    const header = page.locator('#nd-nav');
    await expect(header.getByRole('link', { name: 'GitHub' })).toBeVisible();
    await expect(header.getByRole('button', { name: 'Toggle Theme' })).toHaveCount(0);
    const menu = header.getByRole('button', { name: 'Toggle Menu' });
    if (narrow) {
      await expect(header.getByRole('link', { name: 'About', exact: true })).toBeHidden();
      await menu.click();
    } else {
      await expect(menu).toBeHidden();
    }
    const navigation = narrow ? page.getByRole('dialog') : header;
    for (const [name, href] of [['Docs', '/docs'], ['Set up', '/docs/setup'], ['About', '/about']]) {
      const link = navigation.getByRole('link', { name, exact: true });
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute('href', href);
    }
    await navigation.getByRole('link', { name: 'Set up', exact: true }).click();
    await expect(page).toHaveURL(/\/docs\/setup$/);
  }
});

test('the docs picker combines and copies setup prompts without shifting', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/docs/setup');
  await page.evaluate(() => document.fonts.ready.then(() => null));
  const picker = page.locator('[data-setup-picker]');
  const copy = picker.getByRole('button', { name: 'Copy prompt', exact: true });
  const preview = picker.getByRole('textbox', { name: 'Setup prompt' });
  await expect(picker).toBeVisible();
  await expect(copy).toBeDisabled();
  await expect(preview).toHaveValue('');
  await expect(picker.getByRole('checkbox', { checked: true })).toHaveCount(0);
  const opened = { picker: await dimensions(picker), preview: await dimensions(preview) };

  await picker.getByRole('checkbox', { name: 'Telegram', exact: true }).check();
  expect(await dimensions(preview)).toEqual(opened.preview);
  await picker.getByRole('checkbox', { name: 'Slack', exact: true }).check();
  await expect(picker.getByRole('checkbox', { name: 'Companion', exact: true })).toHaveCount(0);
  await picker.getByRole('checkbox', { name: 'Discord', exact: true }).check();
  const prompt = await preview.inputValue();
  for (const phrase of [
    'Create New App > From a manifest',
    'https://t.me/BotFather',
    'If the SwB tools are missing, ask me to add SwB as a custom MCP server.',
    'switchboard.message.created',
  ]) expect(prompt.split(phrase)).toHaveLength(2);
  expect(prompt.indexOf('Help me run SwB.')).toBeLessThan(prompt.indexOf('### Slack'));
  expect(prompt.indexOf('### Slack')).toBeLessThan(prompt.indexOf('### Telegram'));
  expect(prompt).toContain('### Discord');
  expect(prompt).not.toContain('### Companion');
  expect(await dimensions(picker)).toEqual(opened.picker);
  expect(await dimensions(preview)).toEqual(opened.preview);

  await copy.click();
  await expect(picker.getByRole('status')).toHaveText('Copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(prompt);
  await picker.getByRole('checkbox', { name: 'Telegram', exact: true }).uncheck();
  expect(await preview.inputValue()).not.toContain('https://t.me/BotFather');
  expect(await dimensions(picker)).toEqual(opened.picker);
  expect(await dimensions(preview)).toEqual(opened.preview);
  for (const name of ['Claude', 'Grok Bot', 'GitHub', 'Telegram']) {
    await picker.getByRole('checkbox', { name, exact: true }).check();
  }
  await expect(picker.getByRole('checkbox', { checked: true })).toHaveCount(6);
  expect(await dimensions(picker)).toEqual(opened.picker);
  expect(await dimensions(preview)).toEqual(opened.preview);
  expect(await preview.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
    return node.scrollTop;
  })).toBeGreaterThan(0);
});

test('the hero CTA and all hub apps link to localized Get started routes', async ({ page }) => {
  const apps = {
    Slack: 'slack', Claude: 'claude', 'Capy Captain': 'capy', Dots: 'chatgpt-dots',
    Discord: 'discord', ChatGPT: 'chatgpt-dots', Codex: 'codex', GitHub: 'github',
    Gemini: 'gemini', 'Claude Code': 'claude-code', Grok: 'grok',
    Telegram: 'telegram', 'Grok Bot': 'grok-bot', Muse: 'muse',
  };
  for (const [prefix, title] of [
    ['', 'Set up with agent'], ['/zh-Hans', '让智能体设置'], ['/zh-Hant', '讓代理設定'],
  ]) {
    await page.goto(`${prefix}/`);
    const path = `${prefix}/docs/get-started`;
    const cta = page.getByRole('link', { name: title, exact: true });
    await expect(cta).toHaveAttribute('href', path);
    await expect(cta.locator('svg.lucide-plug')).toHaveCount(1);
    const board = page.locator('.swb-board:visible');
    await expect(board.getByRole('link')).toHaveCount(Object.keys(apps).length);
    for (const [name, id] of Object.entries(apps)) {
      await expect(board.getByRole('link', { name, exact: true })).toHaveAttribute('href', `${path}?agents=${id}`);
    }
    await expect(page.locator('dialog')).toHaveCount(0);
  }
});

for (const [locale, path, heading, label] of [
  ['en', '/docs/setup', 'Set up your apps', 'Setup prompt'],
  ['zh-Hans', '/zh-Hans/docs/setup', '设置应用', '设置提示词'],
  ['zh-Hant', '/zh-Hant/docs/setup', '設定應用程式', '設定提示詞'],
]) {
  test(`${locale} coding agent guides show the SwB server and channel names`, async ({ page }) => {
    const docs = path.replace(/\/setup$/, '');
    await page.goto(`${docs}/setup/coding-agent`);
    await expect(page.locator('pre').filter({ hasText: /^claude mcp add/ })).toContainText('--scope user swb ');

    await page.goto(`${docs}/blocks/coding-agents`);
    await expect(page.locator('pre').filter({ hasText: '/plugin marketplace add' })).toContainText('/plugin install swb@swb');
  });

  test(`${locale} docs setup page uses the localized picker with an English prompt`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    const picker = page.locator('[data-setup-picker]');
    await expect(picker.getByRole('checkbox', { name: 'Companion', exact: true })).toHaveCount(0);
    const muse = picker.getByRole('checkbox', { name: 'Muse', exact: true });
    await expect(muse).toBeVisible();
    await expect(muse).not.toBeChecked();
    const preview = picker.getByRole('textbox', { name: label });
    await page.evaluate(() => document.fonts.ready.then(() => null));
    const start = await dimensions(picker);
    await picker.getByRole('checkbox', { name: 'Grok Bot', exact: true }).check();
    await expect(preview).toHaveValue(/Add a custom connector/);
    await muse.check();
    await expect(preview).toHaveValue(/https:\/\/agent\.meta\.ai\/api\/hatch\/oauth\/callback/);
    await expect(preview).toHaveValue(/connect\(what="muse"\)/);
    expect(await dimensions(picker)).toEqual(start);

    await page.goto(path.replace(/\/setup$/, '/companion'));
    const companionPrompt = page.locator('.swb-prompt');
    await expect(companionPrompt.locator('pre')).toContainText('Call `companion_code`');
  });
}

test('the Muse guide offers the release connector prompt', async ({ page }) => {
  await page.goto('/docs/setup/muse');
  const guide = page.locator('.swb-prompt');
  await expect(guide.locator('pre')).toContainText('https://agent.meta.ai/api/hatch/oauth/callback');
  await expect(guide.getByRole('button', { name: 'Copy prompt' })).toBeVisible();
  await expect(guide.getByRole('link', { name: /Open in/ })).toHaveCount(0);
});
