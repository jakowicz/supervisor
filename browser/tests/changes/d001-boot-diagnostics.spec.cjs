const { test, expect } = require('@playwright/test');

test('D001: the asset-free Emberhold bootstrap loads without browser errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);

  expect(await page.locator('canvas').count()).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});
