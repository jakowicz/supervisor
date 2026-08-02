const { test, expect } = require('@playwright/test');

test('D011: semantic token gallery is reachable without browser errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  // Follow the accessible boot path because Flutter paints most widget detail
  // on canvas. This avoids fragile DOM assertions about canvas internals.
  await page.goto('/', { waitUntil: 'commit' });
  const enableAccessibility =
      page.getByRole('button', { name: 'Enable accessibility' });
  await enableAccessibility.evaluate(element => element.click());

  const diagnostics = page.getByRole('button', { name: 'Run diagnostics' });
  await expect(diagnostics).toBeVisible({ timeout: 10_000 });
  await diagnostics.click();

  // Flutter virtualizes off-screen ListView semantics. On the portrait
  // viewport, scroll the diagnostics list before resolving its final action.
  // This keeps the flow user-realistic while avoiding a desktop-only DOM node.
  await page.mouse.wheel(0, 1_200);

  const gallery = page.getByRole('button', { name: 'Design token gallery' });
  await expect(gallery).toBeVisible({ timeout: 10_000 });
  await gallery.click();
  await expect(page.getByText('Design token gallery', { exact: true }))
      .toBeVisible({ timeout: 10_000 });

  // Widget tests cover exact token mapping and contrast; this verifies the
  // browser build can reach the developer-visible token evidence safely.
  expect(errors).toEqual([]);
});
