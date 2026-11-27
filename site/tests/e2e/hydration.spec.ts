import { expect, test } from '@playwright/test';

for (const [lang, prefix] of [['en', ''], ['zh-Hans', '/zh-Hans'], ['zh-Hant', '/zh-Hant']]) {
  for (const route of ['/', '/docs', '/docs/install', '/docs/get-started']) {
    const path = route === '/' ? prefix || '/' : prefix + route;
    test(`${path} hydrates without browser errors`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(message.text());
      });

      const response = await page.goto(path, { waitUntil: 'networkidle' });
      expect(response?.ok()).toBe(true);
      await expect(page.locator('html')).toHaveAttribute('lang', lang);
      expect(errors).toEqual([]);
    });
  }
}
