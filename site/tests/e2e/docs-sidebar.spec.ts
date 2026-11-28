import { expect, test } from '@playwright/test';

test('groups the docs in the desktop sidebar and mobile drawer', async ({ page }) => {
  await page.goto('/docs/deploy');
  const mobile = page.viewportSize()!.width < 768;
  if (mobile) await page.getByRole('button', { name: 'Open Sidebar', exact: true }).click();

  const sidebar = page.locator(mobile ? '#nd-sidebar-mobile' : '#nd-sidebar');
  const items = sidebar.locator('[data-radix-scroll-area-viewport]').locator('p, hr, a, button');
  const navigation = items.filter({ visible: true });
  await expect(navigation).toHaveText([
    'Get started',
    'Run SwB', 'Install', 'Deploy', 'Cloudflare', 'First run', 'Owner sign-in', 'Operate SwB',
    'Connect agents', 'Connect your agents', 'Set up your apps', 'Companion',
    'Blocks', 'Blocks',
    'Concepts', 'SwB', 'How it works', 'Memory', 'Channel modes',
    'Reference', 'Tools', 'Config', 'Credentials', 'Security',
    '', 'FAQ', 'About',
  ]);
  await expect(sidebar.getByRole('link', { name: 'Deploy', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(sidebar.getByRole('link', { name: 'Cloudflare', exact: true })).toHaveAttribute('href', '/docs/cloudflare');
  await expect(sidebar.getByRole('link', { name: 'Operate SwB', exact: true })).toHaveAttribute('href', '/docs/operate');
  await sidebar.getByRole('button', { name: 'Set up your apps', exact: true }).click();
  await expect(sidebar.getByRole('link', { name: 'Muse', exact: true })).toHaveAttribute('href', '/docs/setup/muse');
  await sidebar.getByRole('button', { name: 'Blocks', exact: true }).click();
  await expect(sidebar.getByRole('link', { name: 'Local sessions', exact: true })).toHaveAttribute('href', '/docs/blocks/local-sessions');
});
