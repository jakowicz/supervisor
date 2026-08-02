const { chromium, devices } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

async function capture(label, device, url, outputDirectory, browser) {
  const context = await browser.newContext({ ...device });
  const page = await context.newPage();
  await page.goto(url, { waitUntil: 'commit' });
  await page.waitForTimeout(1600);
  const destination = path.join(outputDirectory, `${label}.png`);
  await page.screenshot({ path: destination, fullPage: true });
  await context.close();
  console.log(`Captured ${destination}`);
}

async function main() {
  const [url, outputDirectory] = process.argv.slice(2);
  if (!url || !outputDirectory) {
    throw new Error('Usage: capture_visual_evidence.cjs <url> <output-directory>');
  }
  fs.mkdirSync(outputDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    await capture('desktop', { viewport: { width: 1440, height: 1000 } }, url, outputDirectory, browser);
    await capture('mobile', devices['iPhone 13'], url, outputDirectory, browser);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
