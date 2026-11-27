import { expect, test, type Locator, type Page } from '@playwright/test';

async function dimensions(locator: Locator) {
  return locator.evaluate((node) => {
    const { x, y, width, height } = node.getBoundingClientRect();
    return [x + window.scrollX, y + window.scrollY, width, height];
  });
}

async function pageGeometry(page: Page) {
  return {
    hero: await dimensions(page.locator('.swb-hero-copy')),
    height: await page.evaluate(() => document.documentElement.scrollHeight),
  };
}

test('landing and docs share links, with a menu on narrow screens', async ({ page }) => {
  await page.goto('/');
  const header = page.locator('#nd-nav');
  if (page.viewportSize()!.width < 640) {
    await expect(header.getByRole('link', { name: 'About', exact: true })).toBeHidden();
    await header.getByRole('button', { name: 'Toggle Menu' }).click();
  }
  for (const [name, href] of [['Docs', '/docs'], ['Set up', '/docs/setup'], ['About', '/about']]) {
    const link = header.getByRole('link', { name, exact: true });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', href);
  }
  await header.getByRole('link', { name: 'Set up', exact: true }).click();
  await expect(page).toHaveURL(/\/docs\/setup$/);
  await page.goto('/docs');
  const setup = page.locator('header').getByRole('link', { name: 'Set up', exact: true });
  if (page.viewportSize()!.width >= 640) await expect(setup.first()).toBeVisible();
});

test('landing CTA combines and copies selected setup prompts without shifting the page or picker', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready.then(() => null));
  const cta = page.getByRole('button', { name: 'Set up', exact: true });
  const baseline = await pageGeometry(page);
  await cta.click();
  const picker = page.getByRole('dialog', { name: 'Set up' });
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
  const prompt = await preview.inputValue();
  for (const phrase of [
    'Create New App > From a manifest',
    'https://t.me/BotFather',
    'If the SwB tools are missing, ask me to add SwB as a custom MCP server.',
    'switchboard.message.created',
  ]) expect(prompt.split(phrase)).toHaveLength(2);
  expect(prompt.indexOf('Help me run SwB.')).toBeLessThan(prompt.indexOf('### Slack'));
  expect(prompt.indexOf('### Slack')).toBeLessThan(prompt.indexOf('### Telegram'));
  expect(await dimensions(picker)).toEqual(opened.picker);
  expect(await dimensions(preview)).toEqual(opened.preview);
  expect(await pageGeometry(page)).toEqual(baseline);

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
  await expect(picker.getByRole('checkbox', { checked: true })).toHaveCount(5);
  expect(await dimensions(picker)).toEqual(opened.picker);
  expect(await dimensions(preview)).toEqual(opened.preview);
  expect(await preview.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
    return node.scrollTop;
  })).toBeGreaterThan(0);
  await page.keyboard.press('Escape');
  await expect(picker).toBeHidden();
  await expect(cta).toBeFocused();
  expect(await pageGeometry(page)).toEqual(baseline);
});

for (const [locale, path, heading, label] of [
  ['en', '/docs/setup', 'Set up your apps', 'Setup prompt'],
  ['zh-Hans', '/zh-Hans/docs/setup', '设置你的应用', '设置提示词'],
  ['zh-Hant', '/zh-Hant/docs/setup', '設定你的應用程式', '設定提示詞'],
]) {
  test(`${locale} docs setup page uses the localized picker with an English prompt`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    const picker = page.locator('[data-setup-picker]');
    await expect(picker.getByRole('checkbox', { name: 'Muse' })).toHaveCount(0);
    const preview = picker.getByRole('textbox', { name: label });
    await page.evaluate(() => document.fonts.ready.then(() => null));
    const start = await dimensions(picker);
    await picker.getByRole('checkbox', { name: 'Grok Bot', exact: true }).check();
    await expect(preview).toHaveValue(/Add a custom connector/);
    expect(await dimensions(picker)).toEqual(start);
  });
}
