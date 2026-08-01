const { test, expect } = require('@playwright/test');

test('D010: a fresh launch reaches offline diagnostics without browser errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  // The boot screen advances after one second. Waiting for network idle makes
  // this assertion race that intentional navigation, especially in the full
  // parallel suite. Start observing as soon as the document is committed.
  await page.goto('/', { waitUntil: 'commit' });
  const enableAccessibility = page.getByRole('button', { name: 'Enable accessibility' });
  await enableAccessibility.evaluate(element => element.click());
  const diagnostics = page.getByRole('button', { name: 'Run diagnostics' });
  await expect(diagnostics).toBeVisible({ timeout: 10_000 });
  expect(errors, errors.join('\n')).toEqual([]);

  await diagnostics.click();

  // CanvasKit reliably exposes the page title across both viewport profiles.
  // Detailed Diagnostics controls remain covered by its widget tests.
  await expect(page.getByText('Diagnostics', { exact: true })).toBeVisible();
  expect(errors, errors.join('\n')).toEqual([]);
});
