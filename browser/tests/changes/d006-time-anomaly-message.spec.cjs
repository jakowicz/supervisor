const { test, expect } = require('@playwright/test');

const observationKey = 'flutter.emberhold.time_observation';

async function loadApp(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });

  await page.goto('/', { waitUntil: 'networkidle' });
  await expect(page.locator('canvas')).toHaveCount(1);
  return errors;
}

test('D006: first launch writes a versioned local time observation', async ({ page }) => {
  await page.addInitScript(key => localStorage.removeItem(key), observationKey);
  const errors = await loadApp(page);

  await expect
      .poll(() => page.evaluate(key => localStorage.getItem(key), observationKey))
      .not.toBeNull();

  const stored = await page.evaluate(key => {
    // shared_preferences_web JSON-encodes its String values, so decoding once
    // yields the VersionedStore envelope written by the app.
    return JSON.parse(localStorage.getItem(key));
  }, observationKey);
  const envelope = JSON.parse(stored);

  expect(envelope.schemaVersion).toBe(1);
  expect(envelope.payload.epochSeconds).toEqual(expect.any(Number));
  expect(envelope.payload.epochSeconds).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test('D006: a persisted extreme-forward observation never blocks app startup', async ({ page }) => {
  await page.addInitScript(key => {
    const staleEnvelope = JSON.stringify({
      schemaVersion: 1,
      payload: { epochSeconds: 0 },
    });
    localStorage.setItem(key, JSON.stringify(staleEnvelope));
  }, observationKey);

  const errors = await loadApp(page);
  await expect
      .poll(() => page.evaluate(key => localStorage.getItem(key), observationKey))
      .not.toBe(JSON.stringify(JSON.stringify({
        schemaVersion: 1,
        payload: { epochSeconds: 0 },
      })));

  expect(errors).toEqual([]);
});
