import { expect, test, type Page } from '@playwright/test';
import { localize, type Locale } from '../../lib/i18n';

async function openSidebar(page: Page) {
  const mobile = page.viewportSize()!.width < 768;
  if (mobile) await page.locator('[data-docs-actions] button[aria-controls="nd-sidebar-mobile"]').click();
  return page.locator(mobile ? '#nd-sidebar-mobile' : '#nd-sidebar');
}

test('groups the docs in the desktop sidebar and mobile drawer', async ({ page }) => {
  await page.goto('/docs/deploy');
  const sidebar = await openSidebar(page);
  const items = sidebar.locator('[data-radix-scroll-area-viewport]').locator('p, hr, a, button');
  const navigation = items.filter({ visible: true });
  await expect(navigation).toHaveText([
    'Get started',
    'Run SwB', 'Install', 'Deploy', 'Cloudflare', 'First run', 'Owner sign-in', 'Operate SwB',
    'Connect agents', 'Connect your agents', 'Set up your apps', 'Companion',
    'Blocks', 'Overview', 'Claude', 'ChatGPT', 'Capy', 'Grok', 'Coding agents', 'Local sessions',
    'Slack', 'Discord', 'Telegram', 'GitHub App', 'Webhooks',
    'Concepts', 'SwB', 'How it works', 'Memory', 'Channel modes',
    'Reference', 'Tools', 'Config', 'Credentials', 'Security',
    '', 'FAQ', 'About',
  ]);
  await expect(sidebar.getByRole('link', { name: 'Deploy', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(sidebar.getByRole('link', { name: 'Cloudflare', exact: true })).toHaveAttribute('href', '/docs/cloudflare');
  await expect(sidebar.getByRole('link', { name: 'Operate SwB', exact: true })).toHaveAttribute('href', '/docs/operate');
  await expect(sidebar.getByRole('button', { name: 'Blocks', exact: true })).toHaveCount(0);
  await expect(sidebar.getByRole('link', { name: 'Local sessions', exact: true })).toHaveAttribute('href', '/docs/blocks/local-sessions');
  await sidebar.getByRole('button', { name: 'Set up your apps', exact: true }).click();
  await expect(sidebar.getByRole('link', { name: 'Muse', exact: true })).toHaveAttribute('href', '/docs/setup/muse');
});

test('localizes the Blocks overview label only in the sidebar', async ({ page }) => {
  const labels = [
    ['en', 'Overview'], ['zh-Hans', '概览'], ['zh-Hant', '總覽'], ['es', 'Vista general'],
    ['fr', 'Vue d’ensemble'], ['de', 'Übersicht'], ['ko', '개요'], ['ja', '概要'],
  ] as const satisfies ReadonlyArray<readonly [Locale, string]>;
  for (const [lang, label] of labels) {
    const path = localize(lang, '/docs/blocks');
    await page.goto(path);
    const heading = page.locator('#nd-page h1');
    const sidebar = await openSidebar(page);
    const overview = sidebar.getByRole('link', { name: label, exact: true });
    await expect(overview).toHaveAttribute('href', path);
    await expect(overview).toHaveAttribute('aria-current', 'page');
    await expect(heading).not.toHaveText(label);
    if (lang === 'en') await expect(heading).toHaveText('Blocks');
  }
});
