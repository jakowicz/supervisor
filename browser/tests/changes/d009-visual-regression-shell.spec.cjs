// D009: real-browser evidence for the deterministic Flutter golden harness.
//
// Flutter's CanvasKit renderer intentionally does not expose the widget tree as
// DOM elements until accessibility is enabled. Widget-level golden tests own
// pixel comparisons; this spec verifies the release web build at the same
// viewport families, captures review artefacts, and fails on browser errors.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const profiles = [
  ['compact', 360, 640],
  ['standard', 390, 844],
  ['large-phone', 430, 932],
  ['tablet', 768, 1024],
];

for (const [name, width, height] of profiles) {
  test(`D009: release shell renders at the ${name} golden profile`, async ({ page }, testInfo) => {
    const browserErrors = [];
    page.on('console', message => {
      if (message.type() === 'error') browserErrors.push(message.text());
    });
    page.on('pageerror', error => browserErrors.push(error.message));

    await page.setViewportSize({ width, height });
    await page.goto('/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1_500);

    await expect(page.locator('canvas')).not.toHaveCount(0);
    const artifactDir = process.env.QA_ARTIFACT_DIR;
    if (artifactDir) {
      fs.mkdirSync(artifactDir, { recursive: true });
      await page.screenshot({
        path: path.join(artifactDir, `d009-shell-${name}-${testInfo.project.name}.png`),
        fullPage: true,
      });
    }
    expect(browserErrors, browserErrors.join('\n')).toEqual([]);
  });
}
