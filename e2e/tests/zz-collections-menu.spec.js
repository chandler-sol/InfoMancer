const { test, expect } = require('@playwright/test');

const sandboxUrl = process.env.INFOMANCER_E2E_SANDBOX_URL || 'http://127.0.0.1:8788';
const acceptancePassword = 'acceptance-password-123';

async function signIn(page) {
  await page.goto(`${sandboxUrl}/login`);
  await page.getByRole('textbox', { name: 'Username or email' }).fill('acceptance-librarian');
  await page.locator('input[type="password"]').fill(acceptancePassword);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== '/login'),
    page.getByRole('button', { name: 'Sign in' }).click(),
  ]);
}

test('Collections card exposes its action menu on hover', async ({ page }) => {
  await signIn(page);
  await page.goto(`${sandboxUrl}/collections`);

  const directStylesheet = page.locator('link[rel="stylesheet"][href*="collection-menu-visibility.css"]');
  expect(await directStylesheet.count()).toBeGreaterThan(0);

  const name = 'Acceptance Hover Collection';
  const existing = page.locator('.collection-picker-card', { hasText: name });
  if (!(await existing.count())) {
    await page.locator('form.collection-create input[name="name"]').fill(name);
    await Promise.all([
      page.waitForURL((url) => /^\/collections\/\d+$/.test(url.pathname)),
      page.getByRole('button', { name: 'Create collection' }).click(),
    ]);
    await page.goto(`${sandboxUrl}/collections`);
  }

  const card = page.locator('.collection-picker-card', { hasText: name }).first();
  const actions = card.locator('.collection-picker-card-actions');
  const trigger = card.locator('.collection-picker-menu > summary');

  await expect(card).toBeVisible();
  await expect(actions).toBeHidden();

  await card.hover();
  await expect(actions).toBeVisible();
  await expect(trigger).toBeVisible();

  await trigger.click();
  await expect(card.locator('.collection-picker-menu')).toHaveAttribute('open', '');
  await expect(card.getByRole('link', { name: 'Edit collection' })).toBeVisible();
});
