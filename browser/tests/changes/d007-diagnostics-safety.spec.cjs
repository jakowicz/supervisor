const { test, expect } = require('@playwright/test');

async function openDiagnosticsFromFreshLaunch(page) {
  // The boot screen intentionally advances to the shell after one second.
  // Do not wait for network idle here: Flutter keeps network activity alive
  // long enough for that navigation to make the boot-only action disappear.
  await page.goto('/', { waitUntil: 'commit' });
  // Flutter web initially exposes a single semantics-enabler. Activating it
  // is required before Playwright can see the app's accessible controls.
  const enableAccessibility = page.getByRole('button', { name: 'Enable accessibility' });
  await enableAccessibility.evaluate(element => element.click());
  const diagnostics = page.getByRole('button', { name: /run diagnostics/i });
  await expect(diagnostics).toBeVisible({ timeout: 10_000 });
  await diagnostics.click();
  await expect(page.getByText('Diagnostics', { exact: true })).toBeVisible();
}

test('D007: offline diagnostics is player-safe and keyboard reachable', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await openDiagnosticsFromFreshLaunch(page);
  await page.context().setOffline(true);

  // CanvasKit exposes the route title and controls, not every ListView text
  // node, to the browser accessibility tree. Widget tests cover the detailed
  // player-safe fields; this browser check covers the real route transition.
  await expect(page.getByText('Diagnostics', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy support summary' })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.locator(':focus')).toBeVisible();
  expect(errors).toEqual([]);
});

test('D007: the copied support summary is explicitly redacted', async ({ page }) => {
  await openDiagnosticsFromFreshLaunch(page);
  await page.getByRole('button', { name: 'Copy support summary' }).click();
  // Clipboard success/failure UI is platform-dependent in headless Chromium;
  // the unit test verifies redaction. Browser QA confirms the action itself
  // is reachable and does not eject the player from Diagnostics.
  await expect(page.getByText('Diagnostics', { exact: true })).toBeVisible();
});
