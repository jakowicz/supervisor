const { test, expect } = require('@playwright/test');

test('D004: portrait shell renders without browser errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1600);

  expect(await page.locator('canvas').count()).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});
