const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  outputDir: './test-results',
  timeout: 30000,
  expect: { timeout: 8000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  // The legacy scenario in acceptance.spec.js asserts the removed secondary
  // Apply strip. Its replacement lives in bulk-match-progress.spec.js and checks
  // the unified phase-aware workflow card instead.
  grepInvert: /bulk match review gives visible apply feedback and preserves unresolved rows/,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  use: {
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
});
