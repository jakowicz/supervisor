const { defineConfig, devices } = require('@playwright/test');

const startFlutterWebServer =
  process.platform === 'darwin'
    ? '/bin/zsh -ilc "cd ../.. && flutter build web --release && python3 -m http.server 4173 --bind 127.0.0.1 --directory build/web"'
    : 'cd ../.. && flutter build web --release && python3 -m http.server 4173 --bind 127.0.0.1 --directory build/web';

module.exports = defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 30_000 },
  reporter: [['list'], ['json', { outputFile: process.env.PLAYWRIGHT_JSON_OUTPUT_NAME || 'playwright-report.json' }]],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } },
    { name: 'mobile', use: { ...devices['iPhone 13'], browserName: 'chromium' } },
  ],
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: startFlutterWebServer,
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
