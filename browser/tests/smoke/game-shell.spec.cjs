const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

test('boot renders the Flutter Hold shell without browser errors', async ({ page }, testInfo) => {
  const browserErrors = [];
  page.on('console', message => {
    if (message.type() === 'error') browserErrors.push(message.text());
  });
  page.on('pageerror', error => browserErrors.push(error.message));

  await page.goto('/', { waitUntil: 'networkidle' });
  // Flutter's renderer does not expose widget text to Playwright until
  // a full accessibility semantics tree is enabled. The app's own widget tests
  // cover tab semantics; this browser gate verifies the rendered shell,
  // console/page errors, and screenshot evidence at real viewports.
  await page.waitForTimeout(1_500);
  await expect(page.getByText('Your mountain home', { exact: true })).toHaveCount(1);

  const artifactDir = process.env.QA_ARTIFACT_DIR;
  if (artifactDir) {
    fs.mkdirSync(artifactDir, { recursive: true });
    await page.screenshot({ path: path.join(artifactDir, `shell-${testInfo.project.name}.png`), fullPage: true });
  }
  expect(browserErrors, browserErrors.join('\n')).toEqual([]);
});
