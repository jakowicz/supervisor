const { test, expect } = require('@playwright/test');

test('D005: local-settings-enabled shell loads without browser errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  await page.goto('/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1600);

  await expect(page.getByText('Your mountain home', { exact: true })).toHaveCount(1);
  expect(errors).toEqual([]);
});
