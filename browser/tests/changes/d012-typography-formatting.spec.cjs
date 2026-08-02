const { test, expect } = require('@playwright/test');

test('D012: typography token evidence is reachable without browser errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  // Flutter paints most text on canvas. Follow the accessible boot path, then
  // prove the browser build reaches the typography/token evidence. Widget and
  // golden tests own precise scale and locale-format assertions.
  await page.goto('/', { waitUntil: 'commit' });
  const enableAccessibility = page.getByRole('button', { name: 'Enable accessibility' });
  await enableAccessibility.evaluate(element => element.click());

  const diagnostics = page.getByRole('button', { name: 'Run diagnostics' });
  await expect(diagnostics).toBeVisible({ timeout: 10_000 });
  await diagnostics.click();

  // The diagnostics ListView virtualizes this lower action on portrait
  // devices, so scroll before querying it. This mirrors D011's stable route.
  await page.mouse.wheel(0, 1_200);
  const gallery = page.getByRole('button', { name: 'Design token gallery' });
  await expect(gallery).toBeVisible({ timeout: 10_000 });
  await gallery.click();
  await expect(page.getByText('Design token gallery', { exact: true }))
      .toBeVisible({ timeout: 10_000 });

  expect(errors, errors.join('\n')).toEqual([]);
});
