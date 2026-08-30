const { test, expect } = require('@playwright/test');

const sandboxUrl = process.env.INFOMANCER_E2E_SANDBOX_URL || 'http://127.0.0.1:8788';

test('rendered templates use the request CSP nonce consistently', async ({ page }) => {
  await page.goto(`${sandboxUrl}/`);

  const policy = page.locator('meta[http-equiv="Content-Security-Policy"]');
  await expect(policy).toHaveCount(1);
  const content = await policy.getAttribute('content');
  expect(content).toContain("script-src-attr 'none'");
  expect(content).toContain("object-src 'none'");

  // Browsers intentionally hide nonce content from getAttribute('nonce') after
  // parsing. The HTMLElement.nonce property is the supported way to inspect it.
  const scriptNonces = await page.locator('script[nonce]').evaluateAll((nodes) =>
    nodes.map((node) => node.nonce).filter(Boolean),
  );
  expect(scriptNonces.length).toBeGreaterThan(0);
  expect(new Set(scriptNonces).size).toBe(1);

  const nonce = scriptNonces[0];
  expect(content).toContain(`'nonce-${nonce}'`);
  await expect(page.locator('script:not([nonce])')).toHaveCount(0);
});
